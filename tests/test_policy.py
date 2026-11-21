"""条款适用、提前量窗口、席位占用与暂留上限测试。"""
import unittest
from pathlib import Path

from trip_audit.dates import parse_date
from trip_audit.ledger import as_of, build_book
from trip_audit.policy import (
    WINDOW_FREE,
    WINDOW_OUTSIDE,
    open_roots,
    reserve_stress,
    slot_pressure,
    term_eligibility,
    terms_for_booking,
)
from source_book import load

DATA = Path(__file__).resolve().parents[1] / "data" / "samples.json"


class PolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = build_book(load(DATA))
        cls.ledger = as_of(cls.book, parse_date("11-21"))
        cls.records = cls.ledger["records"]

    def test_term_version_resolves_from_booking_date(self):
        self.assertEqual(
            terms_for_booking(self.book, parse_date("11-04")).version, "甲"
        )
        self.assertEqual(
            terms_for_booking(self.book, parse_date("11-10")).version, "乙"
        )
        # 待比较条款 11-25 才生效，11-21 的订单不适用
        self.assertEqual(
            terms_for_booking(self.book, parse_date("11-21")).version, "乙"
        )
        self.assertEqual(
            terms_for_booking(self.book, parse_date("11-25")).version, "待比较"
        )

    def test_free_window_vs_outside_and_refund_ratio(self):
        r6 = term_eligibility(self.book, self.records["r6"])
        self.assertEqual(r6["window"], WINDOW_FREE)
        self.assertEqual(r6["refund_ratio"], 1.0)
        r1 = term_eligibility(self.book, self.records["r1"])
        self.assertEqual(r1["window"], WINDOW_OUTSIDE)
        self.assertEqual(r1["refund_ratio"], 0.75)  # 甲条款窗口外比例
        self.assertTrue(r1["switch_over_quota"])    # 2 次改期 > 1 次额度

    def test_announced_suspension_forces_full_refund_ratio(self):
        r2 = term_eligibility(self.book, self.records["r2"])
        self.assertEqual(r2["refund_ratio"], 1.0)
        self.assertIn("停场公告", r2["basis"])

    def test_open_window_is_not_counted_as_no_refund(self):
        open_records = open_roots(self.book, self.ledger)
        roots = {record.root for record in open_records}
        self.assertEqual(roots, {"r1", "r6", "r7", "r8"})

    def test_committed_occupancy_excludes_refunded_and_checked_in_roots(self):
        pressure = {row["slot"]: row for row in slot_pressure(self.book, self.ledger)}
        # s4: r1(3) + r6(2) = 5，容量 12，剩 7
        self.assertEqual(pressure["s4"]["seats_held"], 5)
        self.assertEqual(pressure["s4"]["seats_free"], 7)
        # s1 已全部退款/改走，不占存量
        self.assertEqual(pressure["s1"]["seats_held"], 0)
        # s7 被已确认改期的 r8 占 4 席，容量 8 剩 4
        self.assertEqual(pressure["s7"]["seats_held"], 4)

    def test_reserve_stress_includes_partial_payment_residual(self):
        rows = {row["month"]: row for row in reserve_stress(self.book, self.ledger)}
        november = rows["11"]
        # t2 部分到账差额 210 + t5 待出款 460 = 670
        self.assertEqual(november["held_amount"], 670.0)
        self.assertEqual(november["arrived_amount"], 600.0)
        self.assertFalse(november["cap_breached"])
        self.assertAlmostEqual(november["cap_utilization"], 0.67, places=2)


if __name__ == "__main__":
    unittest.main()
