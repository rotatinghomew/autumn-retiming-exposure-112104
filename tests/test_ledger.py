"""事件链串联与截点可见性测试。"""
import unittest
from pathlib import Path

from trip_audit.dates import parse_date
from trip_audit.ledger import (
    CERT_KNOWN,
    CERT_PENDING,
    PATH_CHECK_IN,
    PATH_REFUND,
    PATH_RESCHEDULE,
    PATH_WAITING,
    as_of,
    build_book,
    walk_chain,
)
from source_book import load

DATA = Path(__file__).resolve().parents[1] / "data" / "samples.json"


class LedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = build_book(load(DATA))
        cls.ledger = as_of(cls.book, parse_date("11-21"))
        cls.records = cls.ledger["records"]

    def test_reschedule_chain_walks_all_tickets_under_one_root(self):
        self.assertEqual(walk_chain(self.book, "r1"), ["t1", "t1b", "t1c"])
        # 续期凭证不另立根，风险人群只按根计一次
        self.assertEqual(self.book.ticket_to_root["t1c"], "r1")
        self.assertEqual(len(self.records), 8)

    def test_first_path_and_current_slot_follow_visible_chain(self):
        r1 = self.records["r1"]
        self.assertEqual(r1.first_path, PATH_RESCHEDULE)
        self.assertEqual(r1.current_slot, "s4")
        self.assertEqual(r1.current_ticket, "t1c")
        self.assertEqual(r1.reschedule_count, 2)
        self.assertEqual(self.records["r3"].first_path, PATH_CHECK_IN)

    def test_blank_reason_stays_pending_until_memo_arrives(self):
        early = as_of(self.book, parse_date("11-12"))["records"]["r2"]
        self.assertEqual(early.first_path, PATH_WAITING)
        self.assertEqual(early.certainty, CERT_PENDING)
        later = self.records["r2"]
        self.assertEqual(later.first_path, PATH_REFUND)
        self.assertEqual(later.certainty, CERT_KNOWN)
        reasons = [item[0] for item in later.reason_trail]
        self.assertIn(None, reasons)  # 空原因保留为待核，不被补写
        self.assertIn("公告停场", reasons)

    def test_payment_date_is_not_cancel_date_and_open_payment_remains_liability(self):
        r5 = self.records["r5"]
        self.assertEqual(r5.first_effective, parse_date("11-17"))  # 原因发生日
        self.assertEqual(r5.payment_states, ["待出款"])
        self.assertEqual(r5.held_amount, 460.0)  # 未出款不等于不会发生
        r2 = self.records["r2"]
        self.assertEqual(r2.arrived_amount, 210.0)
        self.assertEqual(r2.held_amount, 210.0)  # 部分到账后的差额仍暂留

    def test_cutoff_hides_future_material_without_leakage(self):
        early = as_of(self.book, parse_date("11-12"))
        self.assertEqual(early["hidden_event_count"], 8)
        self.assertEqual(early["hidden_payment_count"], 3)
        # 11-12 看不到 r8 的改期（reported 11-21）
        self.assertEqual(early["records"]["r8"].first_path, PATH_WAITING)
        full = as_of(self.book, parse_date("11-30"))
        self.assertEqual(full["hidden_event_count"], 0)

    def test_announcement_only_counts_when_published_by_cutoff(self):
        # s3 的停场公告 11-17 发布：截点 11-16 不可见，11-18 可见
        before = as_of(self.book, parse_date("11-16"))["records"]["r5"]
        self.assertFalse(before.slot_announced_at_cutoff)
        after = as_of(self.book, parse_date("11-18"))["records"]["r5"]
        self.assertTrue(after.slot_announced_at_cutoff)
        self.assertEqual(after.announcement_reason, "降雨停场")

    def test_residual_held_after_partial_payment(self):
        self.assertEqual(self.records["r4"].held_amount, 0.0)
        self.assertEqual(self.records["r4"].arrived_amount, 390.0)


if __name__ == "__main__":
    unittest.main()
