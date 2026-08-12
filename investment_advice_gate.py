"""Deterministic gate between backtest evidence and investment advice.

The gate is deliberately conservative: a strategy may produce an advice
candidate only after both an untouched out-of-sample comparison and a fair
0050 total-return benchmark are available.  A failed or incomplete report is
never converted into a buy/sell recommendation.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
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
    "execution_accounting_contract_missing": "回測缺少新版成交會計契約。",
    "execution_accounting_incomplete": "回測成交或出場資料不完整。",
    "benchmark_exact_bounds_missing": "0050 基準缺少相同起訖日的完整資料。",
    "cutoff_tie_dependent": "入選截止線同分，結果依賴任意代碼排序。",
    "daily_mdd_limit_unconfigured": "每日最大回撤風險上限尚未事前設定。",
    "active_sample_missing": "有效持倉樣本不足，不能以全現金期間通過。",
}


ACCOUNTING_POLICY = "fixed-equal-weight-slots-v1"
MDD_BASIS = "daily_mark_to_market_including_costs"
BENCHMARK_COST_MODEL = "one exact-boundary buy-and-hold round trip per split"
SUPPORTED_CONFIGURED_RISK_POLICIES: frozenset[tuple[str, str]] = frozenset()


def _accounting_complete(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    accounting = item.get("executionAccounting")
    if not (
        item.get("executionComplete") is True
        and isinstance(accounting, dict)
        and accounting.get("schemaVersion") == 1
        and accounting.get("policyVersion") == ACCOUNTING_POLICY
        and accounting.get("complete") is True
        and isinstance(accounting.get("comparisonFrom"), str)
        and isinstance(accounting.get("comparisonTo"), str)
        and isinstance(accounting.get("comparisonTradingDays"), int)
        and accounting["comparisonTradingDays"] >= 2
        and isinstance(accounting.get("investedPeriods"), int)
        and accounting["investedPeriods"] >= 0
        and accounting.get("unresolvedExitSlots") == 0
        and isinstance(accounting.get("tieBreakDependentSlots"), int)
        and accounting["tieBreakDependentSlots"] >= 0
    ):
        return False
    integer_fields = (
        "scheduledPeriods", "investedPeriods", "targetSlotsTotal", "selectedSlots",
        "filledSlots", "closedSlots", "unfilledEntrySlots", "noCandidateCashSlots",
        "unresolvedExitSlots", "costedRoundTrips", "tieBreakDependentSlots",
    )
    if any(not isinstance(accounting.get(field), int) or accounting[field] < 0 for field in integer_fields):
        return False
    cash_weight = accounting.get("averageCashWeight")
    return bool(
        accounting["closedSlots"] + accounting["unresolvedExitSlots"] == accounting["filledSlots"]
        and accounting["filledSlots"] + accounting["unfilledEntrySlots"] == accounting["selectedSlots"]
        and accounting["selectedSlots"] + accounting["noCandidateCashSlots"] == accounting["targetSlotsTotal"]
        and accounting["costedRoundTrips"] == accounting["closedSlots"]
        and 0 <= accounting["investedPeriods"] <= accounting["scheduledPeriods"]
        and isinstance(cash_weight, (int, float))
        and math.isfinite(float(cash_weight))
        and 0 <= float(cash_weight) <= 1
    )


def _strategy_contract(item: object) -> list[str]:
    blockers: list[str] = []
    if not _accounting_complete(item):
        blockers.append("execution_accounting_contract_missing")
        if isinstance(item, dict) and item.get("executionComplete") is False:
            blockers.append("execution_accounting_incomplete")
        return blockers
    assert isinstance(item, dict)
    accounting = item["executionAccounting"]
    if not isinstance(accounting.get("investedPeriods"), int) or accounting["investedPeriods"] < 1:
        blockers.append("active_sample_missing")
    if item.get("selectionCertified") is not True or accounting.get("tieBreakDependentSlots") != 0:
        blockers.append("cutoff_tie_dependent")
    mdd = item.get("mdd")
    policy_identity = (item.get("riskPolicyVersion"), item.get("riskPolicyHash"))
    if (
        item.get("mddBasis") != MDD_BASIS
        or not isinstance(mdd, (int, float))
        or not math.isfinite(float(mdd))
        or float(mdd) > 0
        or policy_identity not in SUPPORTED_CONFIGURED_RISK_POLICIES
        or item.get("dailyMddLimitConfigured") is not True
        or item.get("dailyMddGatePassed") is not True
        or item.get("riskGateEligible") is not True
    ):
        blockers.append("daily_mdd_limit_unconfigured")
    return blockers


def _same_bounds(left: dict, right: dict) -> bool:
    return all(
        left.get(field) == right.get(field)
        for field in ("comparisonFrom", "comparisonTo", "comparisonTradingDays")
    )


def _rolling_contract(rolling: object) -> list[str]:
    if not isinstance(rolling, dict) or rolling.get("schemaVersion") != 2:
        return ["execution_accounting_contract_missing"]
    windows = rolling.get("windows")
    if not isinstance(windows, list) or len(windows) < 3:
        return ["rolling_validation_not_passed"]
    blockers: list[str] = []
    blockers.extend(rolling.get("blockers") or [])
    identities: set[tuple[object, ...]] = set()
    for window in windows:
        if not isinstance(window, dict) or window.get("passed") is not True:
            blockers.append("one_or_more_rolling_windows_failed")
            continue
        try:
            window_from = date.fromisoformat(window["from"])
            window_to = date.fromisoformat(window["to"])
            validation_from = date.fromisoformat(window["validation"]["executionAccounting"]["comparisonFrom"])
            validation_to = date.fromisoformat(window["validation"]["executionAccounting"]["comparisonTo"])
            test_from = date.fromisoformat(window["test"]["executionAccounting"]["comparisonFrom"])
            test_to = date.fromisoformat(window["test"]["executionAccounting"]["comparisonTo"])
        except (KeyError, TypeError, ValueError):
            blockers.append("rolling_validation_not_passed")
            continue
        if not (window_from < validation_from <= validation_to < test_from <= test_to <= window_to):
            blockers.append("rolling_validation_not_passed")
        identities.add((
            window["from"], window["to"],
            window["validation"]["executionAccounting"]["comparisonFrom"],
            window["validation"]["executionAccounting"]["comparisonTo"],
            window["validation"]["executionAccounting"]["comparisonTradingDays"],
            window["test"]["executionAccounting"]["comparisonFrom"],
            window["test"]["executionAccounting"]["comparisonTo"],
            window["test"]["executionAccounting"]["comparisonTradingDays"],
        ))
        for split_name in ("validation", "test"):
            split = window.get(split_name)
            if not isinstance(split, dict) or not isinstance(split.get("strategy"), (int, float)) or not isinstance(split.get("benchmark"), (int, float)):
                blockers.append("benchmark_exact_bounds_missing")
                continue
            blockers.extend(_strategy_contract(split))
            benchmark_accounting = split.get("benchmarkAccounting")
            if (
                not isinstance(benchmark_accounting, dict)
                or not _same_bounds(split["executionAccounting"], benchmark_accounting)
                or split.get("benchmarkCostModel") != BENCHMARK_COST_MODEL
            ):
                blockers.append("benchmark_exact_bounds_missing")
    if rolling.get("promotionPassed") is not True:
        blockers.extend(rolling.get("blockers") or ["rolling_validation_not_passed"])
    if len(identities) < 3:
        blockers.append("rolling_validation_not_passed")
    return blockers


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
    if one_year.get("schemaVersion") != 2:
        blockers.append("execution_accounting_contract_missing")
    if one_year.get("decision") != "candidate":
        blockers.append("one_year_out_of_sample_failed")
    benchmark = one_year.get("benchmark")
    if not isinstance(benchmark, dict) or not (
        benchmark.get("total_return") is True
        and benchmark.get("executionComplete") is True
        and benchmark.get("cost_model") == BENCHMARK_COST_MODEL
        and isinstance(benchmark.get("validation_net_return"), (int, float))
        and isinstance(benchmark.get("test_net_return"), (int, float))
    ):
        blockers.append("benchmark_not_total_return")
        blockers.append("benchmark_exact_bounds_missing")
    blockers.extend(_strategy_contract(one_year.get("validation")))
    blockers.extend(_strategy_contract(one_year.get("test")))
    if isinstance(benchmark, dict):
        for split_name in ("validation", "test"):
            strategy_item = one_year.get(split_name)
            benchmark_accounting = benchmark.get(f"{split_name}Accounting")
            if (
                not isinstance(strategy_item, dict)
                or not isinstance(benchmark_accounting, dict)
                or not _same_bounds(strategy_item.get("executionAccounting", {}), benchmark_accounting)
            ):
                blockers.append("benchmark_exact_bounds_missing")
    if total_return.get("schemaVersion") != 2:
        blockers.append("execution_accounting_contract_missing")
    if total_return.get("promotionBlocked", True):
        blockers.extend(total_return.get("promotionBlockers") or ["total_return_promotion_blocked"])
    if total_return.get("status") not in {"candidate", "promoted"}:
        blockers.append("total_return_not_candidate")
    splits = total_return.get("splits")
    if not isinstance(splits, dict):
        blockers.append("execution_accounting_contract_missing")
    else:
        for name in ("validation", "test"):
            split = splits.get(name)
            if not isinstance(split, dict):
                blockers.append("execution_accounting_contract_missing")
                continue
            blockers.extend(_strategy_contract(split.get("strategy")))
            benchmark_item = split.get("benchmark0050")
            if not _accounting_complete(benchmark_item):
                blockers.append("benchmark_exact_bounds_missing")
            elif not _same_bounds(
                split["strategy"]["executionAccounting"],
                benchmark_item["executionAccounting"],
            ):
                blockers.append("benchmark_exact_bounds_missing")
    blockers.extend(_rolling_contract(rolling))
    # Preserve order while removing duplicates for a compact report.
    blockers = list(dict.fromkeys(blockers))
    passed = not blockers
    return {
        "schemaVersion": 2,
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
    parser.add_argument("--rolling", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/investment-advice-gate.json"))
    args = parser.parse_args()
    result = evaluate(load(args.one_year), load(args.total_return), load(args.rolling))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
