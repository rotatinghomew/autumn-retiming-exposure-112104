"""样册未覆盖分支的内存构造测试：多原因相继发生、联合饱和。"""
import copy
import unittest
from pathlib import Path

from source_book import load
from trip_audit.dates import parse_date
from trip_audit.ledger import (
    CERT_SEQUENCE,
    as_of,
    build_book,
)
from trip_audit.scenarios import joint_pressure, scenario_pack

DATA = Path(__file__).resolve().parents[1] / "data" / "samples.json"


def _index():
    return copy.deepcopy(load(DATA))


class SyntheticBranchTest(unittest.TestCase):
    def test_two_successive_reasons_are_kept_as_sequence_not_collapsed(self):
        index = _index()
        index["events"].extend(
            [
                {
                    "ticket": "t6", "new_ticket": None, "new_slot": None,
                    "action": "请求退款", "reason": "家庭请求",
                    "effective": "11-20", "reported": "11-20",
                },
                {
                    "ticket": "t6", "new_ticket": None, "new_slot": None,
                    "action": "原因补记", "reason": "公告停场",
                    "effective": "11-21", "reported": "11-21",
                },
            ]
        )
        book = build_book(index)
        record = as_of(book, parse_date("11-21"))["records"]["r6"]
        self.assertEqual(record.certainty, CERT_SEQUENCE)
        trail = [reason for reason, _, _ in record.reason_trail]
        self.assertEqual(trail, ["家庭请求", "公告停场"])
        self.assertTrue(record.has_refund_request)

    def test_joint_saturation_blocks_best_of_both_recommendation(self):
        index = _index()
        # 压缩全部未来承接场次：剩余席位总和低于开放根的 15 席需求
        shrink = {"s4": 5, "s5": 6, "s6": 0, "s7": 4}
        for row in index["slots"]:
            if row["slot"] in shrink:
                row["seats"] = shrink[row["slot"]]
        book = build_book(index)
        ledger = as_of(book, parse_date("11-21"))
        pack = scenario_pack(book, ledger)
        retain = next(
            s for s in pack["scenarios"] if s["scenario"] == "retain_reschedule"
        )
        # s4 剩 0、s5 剩 0、s6 剩 0、s7 剩 0，15 席需求无法承接
        self.assertTrue(retain["capacity_saturated"])
        self.assertEqual(retain["seats_overflow"], 15)
        joint = joint_pressure(book, ledger)
        self.assertTrue(joint["joint_saturation"])
        self.assertIn("不能独立按两边最好情景推荐", joint["recommendation_guard"])

    def test_future_terms_never_apply_to_historical_bookings(self):
        index = _index()
        book = build_book(index)
        ledger = as_of(book, parse_date("11-21"))
        # 待比较条款 11-25 才生效；11-21 截点下所有根仍是原出票条款
        versions = {record.version for record in ledger["records"].values()}
        self.assertNotIn("待比较", versions)


if __name__ == "__main__":
    unittest.main()
