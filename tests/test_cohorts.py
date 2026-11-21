"""队列累计占比、资金汇总与可比性识别测试。"""
import unittest
from pathlib import Path

from trip_audit.cohorts import (
    cumulative_path_share,
    cutoff_table,
    fund_summary,
    lead_bucket,
)
from trip_audit.compare import regime_calendar, term_comparison
from trip_audit.dates import parse_date
from trip_audit.ledger import PATH_REFUND, PATH_WAITING, as_of, build_book
from source_book import load

DATA = Path(__file__).resolve().parents[1] / "data" / "samples.json"


class CohortTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = build_book(load(DATA))
        cls.ledger = as_of(cls.book, parse_date("11-21"))

    def test_lead_buckets(self):
        self.assertEqual(lead_bucket(4), "0-7天")
        self.assertEqual(lead_bucket(10), "8-14天")
        self.assertEqual(lead_bucket(20), "15天及以上")

    def test_cumulative_share_counts_roots_not_renewed_tickets(self):
        series = cumulative_path_share(self.book, self.ledger)
        s1 = next(row for row in series if row["origin_slot"] == "s1" and row["version"] == "甲")
        self.assertEqual(s1["cohort_size"], 1)
        last = s1["series"][-1]
        self.assertEqual(last["by_path"]["改期"]["roots"], 1)
        self.assertEqual(last["cumulative_share"], 1.0)

    def test_cutoff_table_separates_free_window_open_from_no_refund(self):
        table = cutoff_table(self.book, self.ledger)
        s4 = next(row for row in table if row["origin_slot"] == "s4")
        self.assertEqual(s4["path_counts"][PATH_WAITING], 1)
        self.assertEqual(s4["free_window_open_roots"], 1)  # 政策未到期单列
        self.assertEqual(s4["path_share"][PATH_REFUND], 0.0)

    def test_fund_summary_totals(self):
        summary = fund_summary(self.book, self.ledger)
        self.assertEqual(summary["total_roots"], 8)
        self.assertEqual(summary["arrived_amount"], 600.0)
        self.assertEqual(summary["held_amount"], 670.0)
        # 未决开放票面：r6 240 + r7 600 + r1 360 + r8 440
        self.assertEqual(summary["unresolved_obligation_paid"], 1640.0)

    def test_confounded_day_flagged_and_term_without_sample_excluded(self):
        comparison = term_comparison(self.book, self.ledger)
        confounded = {item["date"] for item in comparison["confounded_calendar_days"]}
        self.assertEqual(confounded, {"11-29"})  # R1 与 R2 同日
        self.assertEqual(comparison["terms_without_sales_sample"], ["待比较"])
        grades = {(p["left_slot"], p["right_slot"]): p["grade"] for p in comparison["comparable_pairs"]}
        self.assertEqual(grades[("s1", "s2")], "条件有差异的近似对照")
        # 同 regime 同天气但只有乙条款样本，不可识别条款效应
        self.assertEqual(grades[("s4", "s5")], "不可单独识别条款影响")

    def test_regime_calendar_marks_regimes(self):
        calendar = regime_calendar(self.book)
        self.assertTrue(calendar["11-29"]["confounded"])
        self.assertFalse(calendar["11-14"]["confounded"])


if __name__ == "__main__":
    unittest.main()
