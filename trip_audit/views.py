"""财务、产品、客服共用同一笔未决影响的分角色视图。

同一条根记录在三个视图中以同一 ``root``、同一金额、同一政策依据出现，
只是关注字段不同；政策依据随快照保存，下一个截点可以重看当时的判断。
"""
from __future__ import annotations

from .dates import fmt
from .ledger import (
    CERT_KNOWN,
    CERT_PENDING,
    CERT_SEQUENCE,
    PATH_CHECK_IN,
    PATH_REFUND,
    PATH_WAITING,
)
from .policy import policy_snapshot, term_eligibility, reserve_stress


def _common(record, eligibility):
    return {
        "root": record.root,
        "version": record.version,
        "current_ticket": record.current_ticket,
        "current_slot": record.current_slot,
        "first_path": record.first_path,
        "certainty": record.certainty,
        "paid": record.paid,
        "window": eligibility["window"],
        "policy_basis": eligibility["basis"],
        "first_effective": fmt(record.first_effective),
        "first_reported": fmt(record.first_reported),
    }


def pending_registry(book, ledger):
    """期末未决登记册：财务、产品、客服三份视图共享同一批根。"""
    finance, product, service = [], [], []
    for root in sorted(ledger["records"]):
        record = ledger["records"][root]
        if record.is_terminal and record.first_path == PATH_CHECK_IN:
            continue
        eligibility = term_eligibility(book, record)
        common = _common(record, eligibility)

        finance.append(
            {
                **common,
                "requested_amount": round(record.requested_amount, 2),
                "arrived_amount": round(record.arrived_amount, 2),
                "held_amount": round(record.held_amount, 2),
                "open_payment_amount": round(record.open_payment_amount, 2),
                "payment_states": record.payment_states,
                "refund_ratio_if_apply": eligibility["refund_ratio"],
                "announcement_reason": record.announcement_reason,
            }
        )
        product.append(
            {
                **common,
                "remaining_switches": eligibility["remaining_switches"],
                "switch_over_quota": eligibility["switch_over_quota"],
                "reschedule_count": record.reschedule_count,
                "seats": record.seats,
                "slot_announced": record.slot_announced_at_cutoff,
                "reason_trail": [
                    {"reason": reason, "effective": fmt(effective)}
                    for reason, effective, _ in record.reason_trail
                ],
                "open_choice": record.first_path == PATH_WAITING,
            }
        )
        valid_choices = _service_choices(book, record, eligibility)
        service.append(
            {
                **common,
                "valid_choices": valid_choices,
                "blocked_choices": _service_blocks(book, record, eligibility),
                "announcement_reason": record.announcement_reason,
                "announcement_published": fmt(record.announcement_published),
                "reason_pending": record.certainty == CERT_PENDING,
            }
        )

    return {
        "as_of": fmt(ledger["cutoff"]),
        "shared_roots": sorted(
            record.root
            for record in ledger["records"].values()
            if not (record.is_terminal and record.first_path == PATH_CHECK_IN)
        ),
        "finance_view": finance,
        "product_view": product,
        "service_view": service,
        "reserve_stress": reserve_stress(book, ledger),
        "consistency_rule": "三视图 root、金额、政策依据必须一致；到账日不等于取消原因日。",
    }


def _service_choices(book, record, eligibility):
    """给客服的有效选择：已领退款的不再占场，已确认改期不被重写。"""
    choices = []
    if record.first_path == PATH_REFUND:
        if record.arrived_amount >= record.paid:
            return [{"choice": "退款已全额领取", "actionable": False}]
        choices.append(
            {
                "choice": "跟进剩余退款",
                "actionable": True,
                "expected_ratio": eligibility["refund_ratio"],
            }
        )
        return choices
    if eligibility["remaining_switches"] > 0 and not eligibility["switch_over_quota"]:
        choices.append(
            {
                "choice": "行使改期权益",
                "actionable": True,
                "remaining_switches": eligibility["remaining_switches"],
            }
        )
    if eligibility["window"] == "免费退款窗口内":
        choices.append({"choice": "免费窗口内全额退", "actionable": True})
    elif record.slot_announced_at_cutoff:
        choices.append({"choice": "停场确认全额退", "actionable": True})
    else:
        choices.append(
            {
                "choice": "窗口外按比例退",
                "actionable": True,
                "refund_ratio": eligibility["refund_ratio"],
            }
        )
    return choices


def _service_blocks(book, record, eligibility):
    blocks = []
    if eligibility["switch_over_quota"]:
        blocks.append("改期次数已超出票条款，需协商例外，不能自动再改")
    if record.confirmed_reschedule_count:
        blocks.append("存在已确认改期，任何取消必须先协商订单与资金")
    if record.certainty == CERT_PENDING and record.first_path != PATH_WAITING:
        blocks.append("原因待核，客服不得替客户补写原因")
    return blocks


def replay_basis(book, ledger):
    """保存当时的政策依据快照，供下一个截点重看。"""
    snapshot = policy_snapshot(book, ledger)
    snapshot["leakage_audit"] = {
        "hidden_event_count": ledger["hidden_event_count"],
        "hidden_payment_count": ledger["hidden_payment_count"],
        "rule": "被截点屏蔽的事件与支付不得进入任何统计或预测。",
    }
    snapshot["certainty_legend"] = {
        CERT_KNOWN: "原因已确知（含成行核销）",
        CERT_SEQUENCE: "多个原因相继发生，按轨迹保留全部原因",
        CERT_PENDING: "仍在等待或原因待核，不得当作无退款",
    }
    return snapshot
