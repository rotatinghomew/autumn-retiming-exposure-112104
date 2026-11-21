"""情景测算、联合压力与三角色未决视图测试。"""
import json
import unittest
from pathlib import Path

from trip_audit.dates import parse_date
from trip_audit.ledger import as_of, build_book
from trip_audit.report import build_report
from trip_audit.scenarios import (
    SCENARIO_LATE_REFUND,
    SCENARIO_RETAIN,
    late_refund_scenario,
    retain_scenario,
    scenario_pack,
)
from trip_audit.views import pending_registry, replay_basis
from source_book import load

DATA = Path(__file__).resolve().parents[1] / "data" / "samples.json"


class ScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = build_book(load(DATA))
        cls.ledger = as_of(cls.book, parse_date("11-21"))

    def test_open_batch_uses_same_roots_in_both_scenarios(self):
        retain = retain_scenario(self.book, self.ledger)
        late = late_refund_scenario(self.book, self.ledger)
        self.assertEqual(retain["open_root_count"], late["open_root_count"], 4)
        self.assertEqual(retain["demand_seats"], 15)  # r1 3+r6 2+r7 6+r8 4

    def test_retain_locks_existing_reschedule_rights_first(self):
        retain = retain_scenario(self.book, self.ledger)
        locked = {item["slot"]: item["seats_held"] for item in retain["existing_locked_occupancy"]}
        self.assertEqual(locked, {"s4": 5, "s5": 6, "s7": 4})
        # 总剩余席位 26 >= 需求 15，本批未压满，但天气待定的 s7 带不确定标注
        self.assertFalse(retain["capacity_saturated"])
        self.assertTrue(
            any("待定" in note or "未知" in note for note in retain["uncertainty"])
        )

    def test_late_full_refund_range_and_reserve_spill(self):
        late = late_refund_scenario(self.book, self.ledger)
        self.assertEqual(late["potential_refund_range"]["low"], 0.0)
        self.assertEqual(late["potential_refund_range"]["high"], 1640.0)
        month = late["reserve_month"]
        self.assertEqual(month["already_held"], 670.0)
        self.assertEqual(month["room"], 330.0)
        self.assertTrue(month["breached_at_high"])
        self.assertEqual(month["spill_to_month"], "12")
        self.assertEqual(month["spill_amount_high"], 1310.0)
        self.assertTrue(late["potential_refund_range"]["uncertain_roots"])

    def test_confirmed_reschedule_requires_negotiation_and_is_not_rewritten(self):
        pack = scenario_pack(self.book, self.ledger)
        targets = {item["root"]: item for item in pack["negotiation_required"]}
        self.assertIn("r8", targets)
        self.assertEqual(targets["r8"]["seats"], 4)
        # r8 的历史购买条款仍是乙，没有被待比较条款重写
        self.assertEqual(self.ledger["records"]["r8"].version, "乙")

    def test_joint_pressure_does_not_recommend_best_of_both(self):
        pack = scenario_pack(self.book, self.ledger)
        joint = pack["joint_pressure"]
        self.assertFalse(joint["joint_saturation"])  # 本批席位未压满
        self.assertTrue(joint["reserve_breached_under_late_refund"])


class PendingViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = build_book(load(DATA))
        cls.ledger = as_of(cls.book, parse_date("11-21"))

    def test_three_views_share_same_roots_and_amounts(self):
        registry = pending_registry(self.book, self.ledger)
        finance = {row["root"]: row for row in registry["finance_view"]}
        product = {row["root"]: row for row in registry["product_view"]}
        service = {row["root"]: row for row in registry["service_view"]}
        self.assertEqual(set(finance), set(product))
        self.assertEqual(set(finance), set(service))
        self.assertEqual(finance["r5"]["held_amount"], 460.0)
        self.assertEqual(product["r5"]["seats"], 4)
        # r4 已全额到账，客服不再给占场选择
        self.assertEqual(service["r4"]["valid_choices"][0]["actionable"], False)
        # r1 改期超额度，自动改期被拦截
        self.assertTrue(
            all("改期" not in choice["choice"] for choice in service["r1"]["valid_choices"])
        )

    def test_policy_replay_keeps_basis_and_leakage_audit(self):
        replay = replay_basis(self.book, self.ledger)
        self.assertEqual(replay["as_of"], "11-21")
        self.assertIn("root_eligibility", replay)
        self.assertEqual(replay["leakage_audit"]["hidden_event_count"], 1)
        self.assertEqual(
            replay["root_eligibility"]["r2"]["basis"], "停场公告确认，全额可退"
        )


class ReportTest(unittest.TestCase):
    def test_report_is_serializable_and_carries_cutoff_meta(self):
        report = build_report(DATA, "11-21", "11-25")
        encoded = json.dumps(report, ensure_ascii=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["meta"]["as_of"], "11-21")
        self.assertEqual(decoded["meta"]["new_term_sale_date"], "11-25")
        self.assertEqual(len(decoded["root_records"]), 8)


if __name__ == "__main__":
    unittest.main()
