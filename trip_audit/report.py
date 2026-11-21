"""命令行入口：按截点生成台账、队列、对照、情景与未决视图报告。

示例：

    python3 -m trip_audit.report data/samples.json --cutoff 11-21
    python3 -m trip_audit.report data/samples.json --cutoff 11-26 \\
        --new-term-sale-date 11-25 --out build/report
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cohorts import cumulative_path_share, cutoff_table, fund_summary
from .compare import term_comparison
from .dates import fmt, parse_date
from .ledger import as_of, build_book
from .scenarios import scenario_pack
from .views import pending_registry, replay_basis
from source_book import load

REPORT_FILES = (
    ("ledger.json", "root_records"),
    ("cohorts.json", "cohorts"),
    ("comparison.json", "comparison"),
    ("scenarios.json", "scenarios"),
    ("pending.json", "pending"),
    ("policy_replay.json", "policy_replay"),
)


def build_report(path: Path, cutoff_str: str, new_term_sale_date: str | None = None):
    index = load(path)
    book = build_book(index)
    cutoff = parse_date(cutoff_str)
    ledger = as_of(book, cutoff)

    report = {
        "meta": {
            "source": str(path),
            "as_of": fmt(cutoff),
            "new_term_sale_date": new_term_sale_date,
            "new_term_sale_note": (
                "经营人员选定的新条款适用新购日；仅影响该日之后新购，历史购买条款不重写。"
                if new_term_sale_date
                else None
            ),
        },
        "root_records": {
            root: record.to_dict() for root, record in sorted(ledger["records"].items())
        },
        "leakage_audit": {
            "hidden_event_count": ledger["hidden_event_count"],
            "hidden_payment_count": ledger["hidden_payment_count"],
        },
        "cohorts": {
            "cumulative_path_share": cumulative_path_share(book, ledger),
            "cutoff_table": cutoff_table(book, ledger),
            "fund_summary": fund_summary(book, ledger),
        },
        "comparison": term_comparison(book, ledger),
        "scenarios": scenario_pack(book, ledger),
        "pending": pending_registry(book, ledger),
        "policy_replay": replay_basis(book, ledger),
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="秋假团退改截点分析报告")
    parser.add_argument("path", type=Path, help="交换资料 JSON 路径")
    parser.add_argument("--cutoff", required=True, help="截点日期 MM-DD")
    parser.add_argument(
        "--new-term-sale-date",
        help="可选：新条款适用的新购起始日 MM-DD，仅用于标注生效边界",
    )
    parser.add_argument("--out", type=Path, help="报告输出目录；缺省打印到标准输出")
    args = parser.parse_args(argv)

    report = build_report(args.path, args.cutoff, args.new_term_sale_date)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for filename, key in REPORT_FILES:
            payload = {"meta": report["meta"], key: report[key]}
            if key == "root_records":
                payload["leakage_audit"] = report["leakage_audit"]
            (args.out / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"写出 {args.out / filename}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
