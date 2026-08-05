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
        chosen, _ = select_parameters(train)
        validation_result = run_slice(validation, chosen["lookback"], chosen["count"], chosen["holding"])
        test_result = run_slice(test, chosen["lookback"], chosen["count"], chosen["holding"])
        validation_benchmark = benchmark_total_return(benchmark_path, dates[train_end:validation_end])
        test_benchmark = benchmark_total_return(benchmark_path, dates[validation_end:test_end])
        passed = (
            validation_benchmark is not None
            and test_benchmark is not None
            and validation_result["trades"] >= 5
            and test_result["trades"] >= 5
            and validation_result["return"] > validation_benchmark
            and test_result["return"] > test_benchmark
        )
        windows.append({
            "from": dates[start], "to": dates[test_end - 1],
            "parameters": {key: chosen[key] for key in ("lookback", "count", "holding")},
            "validation": {"strategy": validation_result["return"], "benchmark": validation_benchmark, "trades": validation_result["trades"]},
            "test": {"strategy": test_result["return"], "benchmark": test_benchmark, "trades": test_result["trades"]},
            "passed": passed,
        })
        start += step
    blockers = []
    if len(windows) < 3:
        blockers.append("fewer_than_three_rolling_windows")
    if any(not window["passed"] for window in windows):
        blockers.append("one_or_more_rolling_windows_failed")
    return {
        "schemaVersion": 1,
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
