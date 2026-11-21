"""同一批未来订单在不同退改范围下的资金与改期占位压力。

三个情景共用同一批截点开放根（open roots），且只使用截点可见事实：

- ``baseline``：维持出票条款，改期权益与退款比例按 terms 执行；
- ``retain_reschedule``：保留可改期权益（不开放更晚全退），开放根优先转入
  可承接场次，改期存量权益先占席；
- ``late_full_refund``：允许更晚全额退，开放根可按票面全额退款。

所有依赖尚不确定原因或转移去向的结果都带 ``uncertain`` 标记和取值区间；
改期占位与退款暂留同时压满时给出联合饱和警示，禁止两边各取最好情景。
"""
from __future__ import annotations

from datetime import timedelta

from .dates import fmt, lead_hours, parse_month
from .ledger import PATH_REFUND, PATH_WAITING
from .policy import (
    WINDOW_FREE,
    WINDOW_SLOT_PASSED,
    open_roots,
    slot_pressure,
    term_eligibility,
)

SCENARIO_BASELINE = "baseline"
SCENARIO_RETAIN = "retain_reschedule"
SCENARIO_LATE_REFUND = "late_full_refund"


def _root_view(book, record):
    slot = book.slots[record.current_slot]
    eligibility = term_eligibility(book, record)
    hours = lead_hours(record.cutoff, slot.date)
    return {
        "root": record.root,
        "version": record.version,
        "current_slot": record.current_slot,
        "current_ticket": record.current_ticket,
        "seats": record.seats,
        "paid": record.paid,
        "first_path": record.first_path,
        "slot_date": fmt(slot.date),
        "lead_hours_to_slot": round(hours, 1),
        "window": eligibility["window"],
        "remaining_switches": eligibility["remaining_switches"],
        "switch_over_quota": eligibility["switch_over_quota"],
        "slot_announced": record.slot_announced_at_cutoff,
        "weather_undecided": slot.weather == "待定",
        "confirmed_reschedule_roots": record.confirmed_reschedule_count,
    }


def _destinations(book, ledger):
    """未来可承接场次及其剩余席位（先扣存量权益占用）。"""
    return [
        row
        for row in slot_pressure(book, ledger)
        if book.slots[row["slot"]].date > ledger["cutoff"]
    ]


def _reserve_room(book, ledger, month):
    """某月暂留池剩余额度（截点已占部分先扣）。"""
    from .policy import reserve_stress

    cap_rows = {row["month"]: row for row in reserve_stress(book, ledger)}
    cap = book.reserves.get(month)
    used = cap_rows.get(month, {}).get("held_amount", 0.0)
    return cap, used, (cap - used if cap is not None else None)


def baseline_scenario(book, ledger):
    """情景一：维持现条款。开放根继续占用原场次，退款按条款比例。"""
    roots = open_roots(book, ledger)
    views = [_root_view(book, record) for record in roots]
    refund_low = refund_high = 0.0
    uncertain_roots = []
    for record in roots:
        eligibility = term_eligibility(book, record)
        ratio = eligibility["refund_ratio"] or 0.0
        # 政策未到期不等于无退款：给出 0 到条款比例的区间，上界标不确定
        refund_high += record.paid * ratio
        if record.first_path == PATH_WAITING:
            uncertain_roots.append(record.root)
    reschedule_seats = sum(
        record.seats
        for record in roots
        if record.first_path != PATH_WAITING
    )
    return {
        "scenario": SCENARIO_BASELINE,
        "label": "维持现条款",
        "open_roots": views,
        "potential_refund_range": {
            "low": 0.0,
            "high": round(refund_high, 2),
            "uncertain_roots": sorted(uncertain_roots),
        },
        "reschedule_seats_locked": reschedule_seats,
        "note": "免费窗口内的开放根是未决选择，不记为无退款。",
    }


def retain_scenario(book, ledger):
    """情景二：保留改期权益，开放根转向可承接场次。"""
    roots = open_roots(book, ledger)
    destinations = _destinations(book, ledger)
    total_demand_seats = sum(record.seats for record in roots)
    total_free_seats = sum(row["seats_free"] for row in destinations)

    # 已确认改期的存量权益先占席，不可被新需求挤掉
    locked = [
        {
            "slot": row["slot"],
            "seats_held": row["seats_held"],
            "roots": row["roots"],
        }
        for row in destinations
        if row["seats_held"]
    ]
    served = min(total_demand_seats, total_free_seats)
    overflow = max(total_demand_seats - total_free_seats, 0)

    # 退款只承接席位装不下、或条款已无改期余额的部分；去向未确定，给区间
    over_quota_paid = sum(
        record.paid
        for record in roots
        if term_eligibility(book, record)["remaining_switches"] <= 0
    )
    refundable_terms = sum(
        record.paid * (term_eligibility(book, record)["refund_ratio"] or 0.0)
        for record in roots
    )
    return {
        "scenario": SCENARIO_RETAIN,
        "label": "保留可改期权益",
        "open_root_count": len(roots),
        "demand_seats": total_demand_seats,
        "destinations": [
            {
                "slot": row["slot"],
                "date": row["date"],
                "seats_free": row["seats_free"],
                "weather": row["weather"],
                "regime": row["regime"],
            }
            for row in destinations
        ],
        "existing_locked_occupancy": locked,
        "seats_served_range": {"low": 0, "high": served},
        "seats_overflow": overflow,
        "capacity_saturated": total_free_seats <= total_demand_seats,
        "potential_refund_range": {
            "low": 0.0,
            "high": round(max(refundable_terms, over_quota_paid), 2),
            "uncertain_roots": sorted(record.root for record in roots),
        },
        "uncertainty": [
            "开放根是否实际行使改期未知；天气为待定的场次承载能力未确知。",
            "转移去向按可承接场次剩余席位估算，不代表已确认改期。",
        ],
    }


def late_refund_scenario(book, ledger):
    """情景三：允许更晚全额退，开放根可按票面全额退款。"""
    roots = open_roots(book, ledger)
    full_refund = sum(record.paid for record in roots)
    month = parse_month(f"{ledger['cutoff'].month}")
    cap, used, room = _reserve_room(book, ledger, month)
    next_first = (ledger["cutoff"].replace(day=1) + timedelta(days=32)).replace(day=1)
    spill_month = f"{next_first.month:02d}"
    spill_cap = book.reserves.get(spill_month)
    spill = max(full_refund - (room or 0.0), 0.0) if room is not None else 0.0

    # 若部分家庭仍选改期，退款下界为 0，上界为全额；区间显式标注
    return {
        "scenario": SCENARIO_LATE_REFUND,
        "label": "允许更晚全额退",
        "open_root_count": len(roots),
        "potential_refund_range": {
            "low": 0.0,
            "high": round(full_refund, 2),
            "uncertain_roots": sorted(record.root for record in roots),
        },
        "reserve_month": {
            "month": month,
            "cap": cap,
            "already_held": round(used, 2),
            "room": round(room, 2) if room is not None else None,
            "breached_at_high": room is not None and full_refund > room,
            "spill_to_month": spill_month if spill else None,
            "spill_amount_high": round(spill, 2),
            "spill_month_cap": spill_cap,
        },
        "reschedule_demand_released": True,
        "uncertainty": [
            "更晚全退是否被每个家庭选择未知，退款额落在区间内；",
            "已确认改期的订单不在开放根内，若方案要求取消须另行协商。",
        ],
    }


def joint_pressure(book, ledger):
    """改期占位与退款暂留的联合压力，禁止两边独立取最好情景。"""
    retain = retain_scenario(book, ledger)
    refund = late_refund_scenario(book, ledger)
    capacity_saturated = retain["capacity_saturated"]
    reserve_breached = refund["reserve_month"]["breached_at_high"]
    return {
        "capacity_saturated_under_retain": capacity_saturated,
        "reserve_breached_under_late_refund": reserve_breached,
        "joint_saturation": capacity_saturated and reserve_breached,
        "recommendation_guard": (
            "改期用途与退款暂留同时压满，不能独立按两边最好情景推荐；"
            "需在同一开放根集合上做权衡。"
            if capacity_saturated and reserve_breached
            else "两条约束未同时压满，可按开放根的实际选择滚动复核。"
        ),
    }


def scenario_pack(book, ledger):
    """三情景 + 联合压力 + 协商警示的汇总入口。"""
    negotiation_required = []
    for record in ledger["records"].values():
        if record.confirmed_reschedule_count:
            negotiation_required.append(
                {
                    "root": record.root,
                    "confirmed_reschedules": record.confirmed_reschedule_count,
                    "current_slot": record.current_slot,
                    "seats": record.seats,
                    "paid": record.paid,
                    "rule": "任何方案若取消已确认改期，必须协商订单与资金；历史购买条款不重写。",
                }
            )
    return {
        "as_of": fmt(ledger["cutoff"]),
        "scenarios": [
            baseline_scenario(book, ledger),
            retain_scenario(book, ledger),
            late_refund_scenario(book, ledger),
        ],
        "joint_pressure": joint_pressure(book, ledger),
        "negotiation_required": negotiation_required,
    }
