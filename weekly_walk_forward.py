"""Rolling out-of-sample check for a FinMind fixed research basket.

This is deliberately research-only: it refuses to label a fixed basket as
production advice.  It exists to detect whether a signal survives several
time windows before the advice gate can ever be opened.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from total_return_backtest import (
    BUY_FEE, ETF_SELL_TAX, HOLDING, PICKS, SLIPPAGE_BPS, STOCK_SELL_TAX,
    Series, load, num, run_period, total_return_series,
)


def benchmark_return(series: Series, dates: list[str]) -> float:
    values = [series.values[day] for day in dates if day in series.values]
    if len(values) < 2:
        return 0.0
    gross = values[-1] / values[0] - 1
    return (1 + gross) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) * (1 - BUY_FEE - ETF_SELL_TAX - SLIPPAGE_BPS / 10_000) - 1


def evaluate(cache_dir: Path, benchmark_path: Path, codes: set[str], train_days: int = 250,
             validation_days: int = 150, test_days: int = 150, step: int = 150) -> dict:
    payloads = {path.stem: load(path) for path in (cache_dir / "finmind-backtest-v2" / "stocks").glob("*.json")}
    universe = {code: total_return_series(code, payloads[code]) for code in codes if code in payloads}
    benchmark_rows = load(benchmark_path)
    values = {str(row["date"])[:10]: num(row["total_return"]) for row in benchmark_rows if row.get("date")}
    benchmark = Series("0050_TR", values, 0)
    calendar = sorted(benchmark.values)
    grid = [(lookback, holding, picks, mode) for lookback in (20, 40, 60) for holding in (10, 20) for picks in (3, 5) for mode in ("momentum", "risk_adjusted")]
    windows = []
    start = 0
    while start + train_days + validation_days + test_days <= len(calendar):
        train = calendar[start:start + train_days]
        validation = calendar[start + train_days:start + train_days + validation_days]
        test = calendar[start + train_days + validation_days:start + train_days + validation_days + test_days]
        scored = []
        for lookback, holding, picks, mode in grid:
            result = run_period(universe, train, lookback=lookback, holding=holding, picks_count=picks, ranking_mode=mode)
            scored.append((result["totalReturn"], lookback, holding, picks, mode))
        _, lookback, holding, picks, mode = max(scored, key=lambda item: item[0])
        validation_result = run_period(universe, validation, lookback=lookback, holding=holding, picks_count=picks, ranking_mode=mode)
        test_result = run_period(universe, test, lookback=lookback, holding=holding, picks_count=picks, ranking_mode=mode)
        validation_benchmark = benchmark_return(benchmark, validation)
        test_benchmark = benchmark_return(benchmark, test)
        windows.append({
            "from": train[0], "to": test[-1],
            "parameters": {"lookback": lookback, "holding": holding, "picks": picks, "ranking": mode},
            "validation": {"strategy": validation_result["totalReturn"], "benchmark": validation_benchmark, "periods": validation_result["periods"]},
            "test": {"strategy": test_result["totalReturn"], "benchmark": test_benchmark, "periods": test_result["periods"]},
            "passed": validation_result["periods"] >= 3 and test_result["periods"] >= 3 and validation_result["totalReturn"] > validation_benchmark and test_result["totalReturn"] > test_benchmark,
        })
        start += step
    blockers = []
    if len(windows) < 3:
        blockers.append("fewer_than_three_rolling_windows")
    if any(not item["passed"] for item in windows):
        blockers.append("one_or_more_rolling_windows_failed")
    direct_official = benchmark_path.name.startswith("tai50_official")
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "research_only",
        "promotionPassed": bool(windows) and not blockers,
        "universe": {"stocks": len(universe), "fixedBasket": True, "survivorshipBiasRisk": True},
        "benchmark": {"source": "TWSE official Taiwan 50 total-return index" if direct_official else "reconstructed ETF total-return series", "tradingDays": len(calendar)},
        "windows": windows,
        "blockers": blockers + ["fixed_universe_only"] + ([] if direct_official else ["benchmark_reconstructed_from_etf"]),
        "rule": "每個滾動窗口的驗證期與未觸碰測試期都必須扣除成本後跑贏 0050；至少三個窗口且不得有失敗窗口。",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--codes", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/weekly-walk-forward.json"))
    args = parser.parse_args()
    result = evaluate(args.cache_dir, args.benchmark, {code.strip() for code in args.codes.split(",") if code.strip()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
