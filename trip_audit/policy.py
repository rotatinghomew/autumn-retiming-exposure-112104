"""出票条款的适用判断与资金/席位口径。

条款事实（terms 表，出票时生效，历史购买不被新条款重写）：

- ``free_refund_hours``：团期前该小时数内仍可全额退的窗口下限；
- ``switch_count``：条款允许的改期次数；
- ``refundable``：非停场、窗口外退款的可退比例；
- 停场以停场记录（announcements）确认，确认停场按全额可退处理。
"""
from __future__ import annotations

from .dates import fmt, lead_hours, parse_month
from .ledger import (
    PAY_OPEN_STATES,
    PAY_PAID_STATES,
    PATH_CHECK_IN,
    PATH_REFUND,
    PATH_WAITING,
)

# 窗口标签
WINDOW_FREE = "免费退款窗口内"
WINDOW_OUTSIDE = "免费退款窗口外"
WINDOW_TERMINAL = "已终结"
WINDOW_SLOT_PASSED = "团期已过"

# 政策依据代码
BASIS_SLOT_SUSPENDED = "停场公告确认，全额可退"
BASIS_FREE_WINDOW = "提前量仍在免费退款窗口"
BASIS_PARTIAL = "窗口外按条款比例可退"
BASIS_NO_ACTION = "未触发退改，不能记为无退款"


def terms_for_booking(book, booking_date):
    """购票时点适用的条款：取 first_sale 不晚于购票日的最新版本。"""
    candidates = [
        term
        for term in book.terms.values()
        if term.first_sale <= booking_date
    ]
    if not candidates:
        raise ValueError(f"购票日 {fmt(booking_date)} 早于任何条款生效日")
    return sorted(candidates, key=lambda term: term.first_sale)[-1]


def term_eligibility(book, record):
    """根订单在其截点下的条款适用状态与政策依据。

    只描述事实，不预测：尚未发生的退改属于开放选择，不记成无退款。
    """
    term = book.terms[record.version]
    current_slot = book.slots[record.current_slot] if record.current_slot else None
    base = {
        "root": record.root,
        "version": record.version,
        "switch_count": term.switch_count,
        "free_refund_hours": term.free_refund_hours,
        "refundable": term.refundable,
        "remaining_switches": max(term.switch_count - record.reschedule_count, 0),
        "switch_over_quota": record.reschedule_count > term.switch_count,
    }

    if record.is_terminal:
        return {
            **base,
            "window": WINDOW_TERMINAL,
            "refund_ratio": None,
            "basis": "已核销成行，退改链终结",
        }
    if record.first_path == PATH_REFUND:
        ratio = (
            1.0
            if record.slot_announced_at_cutoff
            else term.refundable
        )
        basis = (
            BASIS_SLOT_SUSPENDED
            if record.slot_announced_at_cutoff
            else BASIS_PARTIAL
        )
        return {**base, "window": WINDOW_OUTSIDE, "refund_ratio": ratio, "basis": basis}

    if current_slot is None:
        return {**base, "window": WINDOW_OUTSIDE, "refund_ratio": term.refundable, "basis": BASIS_PARTIAL}

    if current_slot.date <= record.cutoff:
        return {
            **base,
            "window": WINDOW_SLOT_PASSED,
            "refund_ratio": None,
            "basis": "团期已过且未见核销，属于期末未决",
        }

    hours = lead_hours(record.cutoff, current_slot.date)
    announced = record.slot_announced_at_cutoff
    if announced:
        return {
            **base,
            "window": WINDOW_OUTSIDE,
            "refund_ratio": 1.0,
            "basis": BASIS_SLOT_SUSPENDED,
            "lead_hours_to_slot": round(hours, 1),
        }
    if hours >= term.free_refund_hours:
        return {
            **base,
            "window": WINDOW_FREE,
            "refund_ratio": 1.0,
            "basis": BASIS_FREE_WINDOW,
            "lead_hours_to_slot": round(hours, 1),
        }
    return {
        **base,
        "window": WINDOW_OUTSIDE,
        "refund_ratio": term.refundable,
        "basis": BASIS_PARTIAL,
        "lead_hours_to_slot": round(hours, 1),
    }


def refund_ratio(book, record):
    """该根在截点下若退款适用的可退比例；未触发退款时返回条款比例供情景使用。"""
    return term_eligibility(book, record)["refund_ratio"]


def committed_occupancy(book, ledger, slot_id):
    """某团期在截点下被存量权益实际占用的席位数。

    只统计当前凭证落在该团期、且未核销未退款终结的根；续期凭证不重复计。
    """
    seats = 0
    roots_here = []
    for root, record in ledger["records"].items():
        if record.current_slot != slot_id:
            continue
        if record.is_terminal or record.first_path == PATH_REFUND:
            continue
        seats += record.seats
        roots_here.append(root)
    return {"slot": slot_id, "seats_held": seats, "roots": sorted(roots_here)}


def slot_pressure(book, ledger):
    """全部团期的存量占位与剩余可承接席位。"""
    rows = []
    for slot_id, slot in sorted(book.slots.items(), key=lambda item: item[1].date):
        occupancy = committed_occupancy(book, ledger, slot_id)
        rows.append(
            {
                "slot": slot_id,
                "city": slot.city,
                "date": fmt(slot.date),
                "seats": slot.seats,
                "seats_held": occupancy["seats_held"],
                "seats_free": slot.seats - occupancy["seats_held"],
                "weather": slot.weather,
                "regime": slot.regime,
                "roots": occupancy["roots"],
            }
        )
    return rows


def reserve_stress(book, ledger):
    """按月汇总退款暂留与合作方共同上限的对照。

    到账金额是已离开暂留池的损失；待出款与请求未付仍占暂留额度。
    """
    months = {}
    for record in ledger["records"].values():
        if not record.has_refund_request:
            continue
        seen_payments = set()
        for ticket in record.ticket_chain:
            for payment in book.payments_by_ticket.get(ticket, []):
                key = (payment.ticket, payment.amount, payment.state, payment.date)
                if key in seen_payments:
                    continue
                seen_payments.add(key)
            month = parse_month(f"{payment.date.month}")
            bucket = months.setdefault(
                month,
                {"arrived": 0.0, "open": 0.0, "cap": book.reserves.get(month)},
            )
            if payment.state in PAY_PAID_STATES:
                bucket["arrived"] += payment.amount
            elif payment.state in PAY_OPEN_STATES:
                bucket["open"] += payment.amount

    # 请求已发生但尚无批次的暂留款，挂在截点所在月
    cutoff_month = parse_month(f"{ledger['cutoff'].month}")
    for record in ledger["records"].values():
        if not record.has_refund_request:
            continue
        covered_open = sum(
            payment.amount
            for ticket in record.ticket_chain
            for payment in book.payments_by_ticket.get(ticket, [])
            if payment.state in PAY_OPEN_STATES
        )
        # 部分到账后的差额、或请求后尚无批次的金额，仍占截点月暂留额度
        residual = record.held_amount - covered_open
        if residual > 0:
            bucket = months.setdefault(
                cutoff_month,
                {"arrived": 0.0, "open": 0.0, "cap": book.reserves.get(cutoff_month)},
            )
            bucket["open"] += residual

    rows = []
    for month in sorted(months):
        bucket = months[month]
        cap = bucket["cap"]
        held = bucket["open"]
        rows.append(
            {
                "month": month,
                "arrived_amount": round(bucket["arrived"], 2),
                "held_amount": round(held, 2),
                "reserve_cap": cap,
                "cap_utilization": round(held / cap, 4) if cap else None,
                "cap_breached": cap is not None and held > cap,
            }
        )
    return rows


def policy_snapshot(book, ledger):
    """重看截点时可追溯的政策依据：条款版本事实 + 每根的适用判断。"""
    terms = [
        {
            "version": term.version,
            "first_sale": fmt(term.first_sale),
            "free_refund_hours": term.free_refund_hours,
            "switch_count": term.switch_count,
            "refundable": term.refundable,
        }
        for term in sorted(book.terms.values(), key=lambda t: t.first_sale)
    ]
    per_root = {
        root: term_eligibility(book, record)
        for root, record in sorted(ledger["records"].items())
    }
    return {"as_of": fmt(ledger["cutoff"]), "terms": terms, "root_eligibility": per_root}


def open_roots(book, ledger):
    """截点下仍开放（未核销、未进入退款路径）的根，供情景分析使用。"""
    from .ledger import PATH_RESCHEDULE

    return [
        record
        for record in ledger["records"].values()
        if record.first_path in (PATH_WAITING, PATH_RESCHEDULE)
        and not record.is_terminal
    ]
