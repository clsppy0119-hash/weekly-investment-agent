"""Rolling, leakage-resistant promotion check for the official TWSE dataset."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from backtest import benchmark_total_return, load_history, run_slice, select_parameters


def evaluate(history_path: Path, benchmark_path: Path, train_days: int = 120, validation_days: int = 60, test_days: int = 60, step: int = 60) -> dict:
    dates, history = load_history(history_path)
    windows = []
    start = 0
    while start + train_days + validation_days + test_days <= len(history):
        train_end = start + train_days
        validation_end = train_end + validation_days
        test_end = validation_end + test_days
        train = history[start:train_end]
        validation = history[train_end:validation_end]
        test = history[validation_end:test_end]
        train_dates = dates[start:train_end]
        validation_dates = dates[train_end:validation_end]
        test_dates = dates[validation_end:test_end]
        try:
            chosen, _ = select_parameters(train, train_dates)
        except ValueError as exc:
            if str(exc) != "execution_accounting_incomplete":
                raise
            windows.append({
                "from": dates[start], "to": dates[test_end - 1],
                "parameters": None,
                "validation": None,
                "test": None,
                "passed": False,
                "blockers": ["execution_accounting_incomplete"],
            })
            start += step
            continue
        validation_result = run_slice(validation, chosen["lookback"], chosen["count"], chosen["holding"], validation_dates)
        test_result = run_slice(test, chosen["lookback"], chosen["count"], chosen["holding"], test_dates)
        validation_bounds = [validation_result["executionAccounting"]["comparisonFrom"], validation_result["executionAccounting"]["comparisonTo"]]
        test_bounds = [test_result["executionAccounting"]["comparisonFrom"], test_result["executionAccounting"]["comparisonTo"]]
        validation_path = validation_dates[
            validation_dates.index(validation_bounds[0]):validation_dates.index(validation_bounds[1]) + 1
        ] if all(day in validation_dates for day in validation_bounds) else []
        test_path = test_dates[
            test_dates.index(test_bounds[0]):test_dates.index(test_bounds[1]) + 1
        ] if all(day in test_dates for day in test_bounds) else []
        validation_benchmark = benchmark_total_return(benchmark_path, validation_path)
        test_benchmark = benchmark_total_return(benchmark_path, test_path)
        passed = (
            validation_result["executionComplete"]
            and test_result["executionComplete"]
            and validation_result["selectionCertified"]
            and test_result["selectionCertified"]
            and validation_result["dailyMddGatePassed"]
            and test_result["dailyMddGatePassed"]
            and validation_benchmark is not None
            and test_benchmark is not None
            and validation_result["trades"] >= 5
            and test_result["trades"] >= 5
            and validation_result["return"] > validation_benchmark
            and test_result["return"] > test_benchmark
        )
        windows.append({
            "from": dates[start], "to": dates[test_end - 1],
            "parameters": {key: chosen[key] for key in ("lookback", "count", "holding")},
            "validation": {"strategy": validation_result["return"], "benchmark": validation_benchmark, "benchmarkAccounting": {key: validation_result["executionAccounting"][key] for key in ("comparisonFrom", "comparisonTo", "comparisonTradingDays")}, "benchmarkCostModel": "one exact-boundary buy-and-hold round trip per split", "trades": validation_result["trades"], "mdd": validation_result["mdd"], "mddBasis": validation_result["mddBasis"], "executionComplete": validation_result["executionComplete"], "selectionCertified": validation_result["selectionCertified"], "riskPolicyVersion": validation_result["riskPolicyVersion"], "dailyMddLimitConfigured": validation_result["dailyMddLimitConfigured"], "dailyMddGatePassed": validation_result["dailyMddGatePassed"], "riskGateEligible": validation_result["riskGateEligible"], "executionAccounting": validation_result["executionAccounting"]},
            "test": {"strategy": test_result["return"], "benchmark": test_benchmark, "benchmarkAccounting": {key: test_result["executionAccounting"][key] for key in ("comparisonFrom", "comparisonTo", "comparisonTradingDays")}, "benchmarkCostModel": "one exact-boundary buy-and-hold round trip per split", "trades": test_result["trades"], "mdd": test_result["mdd"], "mddBasis": test_result["mddBasis"], "executionComplete": test_result["executionComplete"], "selectionCertified": test_result["selectionCertified"], "riskPolicyVersion": test_result["riskPolicyVersion"], "dailyMddLimitConfigured": test_result["dailyMddLimitConfigured"], "dailyMddGatePassed": test_result["dailyMddGatePassed"], "riskGateEligible": test_result["riskGateEligible"], "executionAccounting": test_result["executionAccounting"]},
            "passed": passed,
            "blockers": [
                blocker for blocker, active in {
                    "execution_accounting_incomplete": not (
                        validation_result["executionComplete"] and test_result["executionComplete"]
                    ),
                    "cutoff_tie_dependent": not (
                        validation_result["selectionCertified"] and test_result["selectionCertified"]
                    ),
                    "daily_mdd_limit_unconfigured": not (
                        validation_result["dailyMddGatePassed"] and test_result["dailyMddGatePassed"]
                    ),
                    "benchmark_exact_bounds_missing": validation_benchmark is None or test_benchmark is None,
                }.items() if active
            ],
        })
        start += step
    blockers = []
    if len(windows) < 3:
        blockers.append("fewer_than_three_rolling_windows")
    if any(not window["passed"] for window in windows):
        blockers.append("one_or_more_rolling_windows_failed")
    if any("execution_accounting_incomplete" in window.get("blockers", []) for window in windows):
        blockers.append("execution_accounting_incomplete")
    if any("cutoff_tie_dependent" in window.get("blockers", []) for window in windows):
        blockers.append("cutoff_tie_dependent")
    if any("daily_mdd_limit_unconfigured" in window.get("blockers", []) for window in windows):
        blockers.append("daily_mdd_limit_unconfigured")
    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "candidate" if windows and not blockers else "research_only",
        "promotionPassed": bool(windows) and not blockers,
        "windows": windows,
        "blockers": blockers,
        "rule": "每個滾動窗口的驗證期與保留測試期都必須淨報酬跑贏官方臺灣50報酬指數，且至少三個窗口",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/walk-forward-status.json"))
    args = parser.parse_args()
    result = evaluate(args.history, args.benchmark)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
