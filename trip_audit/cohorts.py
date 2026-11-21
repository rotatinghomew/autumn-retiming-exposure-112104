"""按团期、提前量、条款版本比较首入退改路径的累计占比与资金。

口径约束：

- 分母是购买根（root），续期凭证不重复计入风险人群；
- 累计占比按首入动作的业务生效日（effective）推进，可见性仍由截点把关；
- “政策未到期”（仍在免费退款窗口、尚无动作）单列为开放，不得并入“无退款”；
- 只用截点前已登记资料，不拿成行以后的资料回填当时的判断。
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict

from .dates import fmt
from .ledger import (
    CERT_KNOWN,
    CERT_PENDING,
    CERT_SEQUENCE,
    PATH_CHECK_IN,
    PATH_REFUND,
    PATH_RESCHEDULE,
    PATH_WAITING,
)
from .policy import WINDOW_FREE, term_eligibility

PATHS = (PATH_REFUND, PATH_RESCHEDULE, PATH_CHECK_IN, PATH_WAITING)


def lead_bucket(days_ahead: int) -> str:
    """购票提前量分桶。"""
    if days_ahead <= 7:
        return "0-7天"
    if days_ahead <= 14:
        return "8-14天"
    return "15天及以上"


def cohort_key(book, record):
    slot = book.slots[record.origin_slot]
    days_ahead = (slot.date - record.booked).days
    return (
        slot.date,
        record.origin_slot,
        slot.city,
        record.version,
        days_ahead,
        lead_bucket(days_ahead),
    )


def _ordered_cohorts(book, records):
    cohorts = OrderedDict()
    for root in sorted(records, key=lambda r: (records[r].booked, r)):
        record = records[root]
        date, slot_id, city, version, days_ahead, bucket = cohort_key(book, record)
        key = (fmt(date), slot_id, city, version)
        cohorts.setdefault(
            key,
            {
                "slot_date": fmt(date),
                "origin_slot": slot_id,
                "city": city,
                "version": version,
                "lead_days": days_ahead,
                "lead_bucket": bucket,
                "roots": [],
            },
        )["roots"].append(root)
    return cohorts


def cumulative_path_share(book, ledger):
    """每个分组首入不同退改路径的累计占比时间序列。

    序列点取该分组内出现过的生效日；到某日的累计人数只含截点可见动作。
    """
    records = ledger["records"]
    cohorts = _ordered_cohorts(book, records)
    result = []
    for key, cohort in cohorts.items():
        members = [records[root] for root in cohort["roots"]]
        size = len(members)
        event_days = sorted(
            {
                record.first_effective
                for record in members
                if record.first_effective is not None
            }
        )
        series = []
        for day in event_days:
            counts = {path: 0 for path in PATHS}
            for record in members:
                if record.first_effective is not None and record.first_effective <= day:
                    counts[record.first_path] += 1
            entered = size - counts[PATH_WAITING]
            series.append(
                {
                    "date": fmt(day),
                    "entered_roots": entered,
                    "cumulative_share": round(entered / size, 4),
                    "by_path": {
                        path: {
                            "roots": counts[path],
                            "share": round(counts[path] / size, 4),
                        }
                        for path in PATHS
                    },
                }
            )
        result.append({**{k: v for k, v in cohort.items() if k != "roots"}, "cohort_size": size, "series": series})
    return result


def cutoff_table(book, ledger):
    """截点期末视图：首入路径、原因确定性、留置资金、未决范围。"""
    records = ledger["records"]
    cohorts = _ordered_cohorts(book, records)
    table = []
    for key, cohort in cohorts.items():
        members = [records[root] for root in cohort["roots"]]
        size = len(members)
        path_counts = {path: 0 for path in PATHS}
        cert_counts = {
            CERT_KNOWN: 0,
            CERT_SEQUENCE: 0,
            CERT_PENDING: 0,
        }
        arrived = held = open_pay = 0.0
        open_free_window = 0
        unresolved = []
        for record in members:
            path_counts[record.first_path] += 1
            cert_counts[record.certainty] += 1
            arrived += record.arrived_amount
            held += record.held_amount
            open_pay += record.open_payment_amount
            eligibility = term_eligibility(book, record)
            if (
                record.first_path == PATH_WAITING
                and eligibility["window"] == WINDOW_FREE
            ):
                open_free_window += 1
            if not record.is_terminal:
                unresolved.append(
                    {
                        "root": record.root,
                        "first_path": record.first_path,
                        "certainty": record.certainty,
                        "held_amount": round(record.held_amount, 2),
                        "window": eligibility["window"],
                        "basis": eligibility["basis"],
                    }
                )
        table.append(
            {
                "slot_date": cohort["slot_date"],
                "origin_slot": cohort["origin_slot"],
                "city": cohort["city"],
                "version": cohort["version"],
                "lead_days": cohort["lead_days"],
                "lead_bucket": cohort["lead_bucket"],
                "cohort_size": size,
                "path_counts": path_counts,
                "path_share": {
                    path: round(path_counts[path] / size, 4) for path in PATHS
                },
                "certainty_counts": cert_counts,
                "arrived_amount": round(arrived, 2),
                "held_amount": round(held, 2),
                "open_payment_amount": round(open_pay, 2),
                "free_window_open_roots": open_free_window,
                "unresolved_roots": unresolved,
            }
        )
    return table


def fund_summary(book, ledger):
    """全量资金口径：已到账损失、暂留款、期末未决票面。"""
    records = list(ledger["records"].values())
    arrived = sum(r.arrived_amount for r in records)
    held = sum(r.held_amount for r in records)
    open_payment = sum(r.open_payment_amount for r in records)
    unresolved_paid = sum(
        r.paid for r in records if not r.is_terminal and not r.has_refund_request
    )
    by_path = defaultdict(lambda: {"roots": 0, "paid": 0.0, "held": 0.0, "arrived": 0.0})
    for record in records:
        bucket = by_path[record.first_path]
        bucket["roots"] += 1
        bucket["paid"] += record.paid
        bucket["held"] += record.held_amount
        bucket["arrived"] += record.arrived_amount
    return {
        "total_roots": len(records),
        "arrived_amount": round(arrived, 2),
        "held_amount": round(held, 2),
        "open_payment_amount": round(open_payment, 2),
        "unresolved_obligation_paid": round(unresolved_paid, 2),
        "by_first_path": {
            path: {
                "roots": bucket["roots"],
                "paid": round(bucket["paid"], 2),
                "held": round(bucket["held"], 2),
                "arrived": round(bucket["arrived"], 2),
            }
            for path, bucket in sorted(by_path.items())
        },
    }
