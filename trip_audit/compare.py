"""新旧条款对照与可比性识别。

识别规则：

- 同一城市、同一停场规则标识（regime）、天气条件不冲突的团期，才构成
  “外部条件相近”的跨条款对照；
- 同一日历日若同时存在不同 regime 的团期，说明停场规则与条款一起变了，
  该日标记为混淆日，不足以单独识别条款影响；
- 没有成交样本的待比较条款只能进入情景测算，不能伪造对照结论。
"""
from __future__ import annotations

from collections import defaultdict

from .cohorts import cutoff_table
from .dates import fmt


def regime_calendar(book):
    """日历日 -> 当天出现过的 regime 集合与天气集合。"""
    days = defaultdict(lambda: {"regimes": set(), "weathers": set(), "slots": []})
    for slot in book.slots.values():
        day = days[fmt(slot.date)]
        day["regimes"].add(slot.regime)
        day["weathers"].add(slot.weather)
        day["slots"].append(slot.slot)
    return {
        day: {
            "regimes": sorted(info["regimes"]),
            "weathers": sorted(info["weathers"]),
            "slots": sorted(info["slots"]),
            "confounded": len(info["regimes"]) > 1,
        }
        for day, info in sorted(days.items())
    }


def comparable_pairs(book, ledger):
    """生成跨条款可比团期对与混淆警示。

    两级口径：城市与 regime 一致即进入候选；天气也一致为“严格相近”，
    天气不同则保留为“条件有差异的近似对照”，不得当作严格对照。
    """
    calendar = regime_calendar(book)
    slots = sorted(book.slots.values(), key=lambda s: (s.date, s.slot))
    pairs = []
    used = set()
    for i, left in enumerate(slots):
        for right in slots[i + 1 :]:
            if not (left.city == right.city and left.regime == right.regime):
                continue
            key = tuple(sorted((left.slot, right.slot)))
            if key in used:
                continue
            used.add(key)
            versions = sorted(
                {
                    ledger["records"][root].version
                    for root, record in ledger["records"].items()
                    if record.origin_slot in (left.slot, right.slot)
                }
            )
            same_day_confounded = calendar[fmt(left.date)]["confounded"] or calendar[
                fmt(right.date)
            ]["confounded"]
            weather_match = left.weather == right.weather
            cross_term = len(set(versions)) >= 2
            if weather_match and cross_term and not same_day_confounded:
                grade = "严格相近"
                caution = None
            elif cross_term and not weather_match:
                grade = "条件有差异的近似对照"
                caution = "天气条件不同（{} vs {}），只能作近似对照".format(
                    left.weather, right.weather
                )
            else:
                grade = "不可单独识别条款影响"
                caution = "缺少跨条款样本或同一日历日停场规则同时变动"
            pairs.append(
                {
                    "left_slot": left.slot,
                    "right_slot": right.slot,
                    "city": left.city,
                    "dates": [fmt(left.date), fmt(right.date)],
                    "regime": left.regime,
                    "weather": [left.weather, right.weather],
                    "weather_match": weather_match,
                    "observed_versions": versions,
                    "cross_term": cross_term,
                    "grade": grade,
                    "identifiable": grade == "严格相近",
                    "caution": caution,
                }
            )
    return pairs


def term_comparison(book, ledger):
    """条款对照主入口：分组事实 + 可比性 + 混淆日 + 无样本条款声明。"""
    table = cutoff_table(book, ledger)
    observed_versions = sorted({row["version"] for row in table})
    calendar = regime_calendar(book)
    confounded_days = [day for day, info in calendar.items() if info["confounded"]]
    no_sample_terms = [
        term.version
        for term in book.terms.values()
        if term.version not in observed_versions
    ]
    return {
        "observed_versions": observed_versions,
        "terms_without_sales_sample": no_sample_terms,
        "confounded_calendar_days": [
            {"date": day, **calendar[day]} for day in confounded_days
        ],
        "comparable_pairs": comparable_pairs(book, ledger),
        "cohort_facts": table,
        "note": "可比对要求城市、regime、天气一致；混淆日与无样本条款不得用于识别条款效应。",
    }
