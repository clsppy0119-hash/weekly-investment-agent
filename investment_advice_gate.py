"""Deterministic gate between backtest evidence and investment advice.

The gate is deliberately conservative: a strategy may produce an advice
candidate only after both an untouched out-of-sample comparison and a fair
0050 total-return benchmark are available.  A failed or incomplete report is
never converted into a buy/sell recommendation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone


BLOCKER_LABELS = {
    "one_year_out_of_sample_failed": "官方行情保留測試未通過。",
    "benchmark_not_total_return": "0050 基準不是官方總報酬資料。",
    "survivorship_bias": "股票範圍仍有生存者偏差。",
    "total_return_promotion_blocked": "總報酬回測尚未通過升級門檻。",
    "total_return_not_candidate": "總報酬策略尚未成為候選策略。",
    "fewer_than_three_rolling_windows": "滾動樣本外視窗少於三個。",
    "one_or_more_rolling_windows_failed": "至少一個滾動樣本外視窗未跑贏 0050。",
    "rolling_validation_not_passed": "滾動樣本外驗證尚未通過。",
}


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def evaluate(one_year: dict, total_return: dict, rolling: dict | None = None) -> dict:
    blockers: list[str] = []
    if one_year.get("decision") != "candidate":
        blockers.append("one_year_out_of_sample_failed")
    if not one_year.get("benchmark", {}).get("total_return"):
        blockers.append("benchmark_not_total_return")
    if total_return.get("promotionBlocked", True):
        blockers.extend(total_return.get("promotionBlockers") or ["total_return_promotion_blocked"])
    if total_return.get("status") not in {"candidate", "promoted"}:
        blockers.append("total_return_not_candidate")
    if rolling is not None and not rolling.get("promotionPassed", False):
        blockers.extend(rolling.get("blockers") or ["rolling_validation_not_passed"])
    # Preserve order while removing duplicates for a compact report.
    blockers = list(dict.fromkeys(blockers))
    passed = not blockers
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "advice_candidate" if passed else "research_only",
        "adviceEnabled": passed,
        "verdict": "可以產生條件式建議" if passed else "禁止產生買賣建議",
        "blockers": blockers,
        "blockerDetails": [
            {"code": code, "message": BLOCKER_LABELS.get(code, code)}
            for code in blockers
        ],
        "rule": "只有公平0050總報酬基準、未觸碰測試集通過，且總報酬研究閘門開啟時才可建議",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--one-year", type=Path, default=Path("backtest_data/one_year_backtest.json"))
    parser.add_argument("--total-return", type=Path, default=Path("data/total-return-backtest-status.json"))
    parser.add_argument("--rolling", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("data/investment-advice-gate.json"))
    args = parser.parse_args()
    result = evaluate(load(args.one_year), load(args.total_return), load(args.rolling) if args.rolling else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
