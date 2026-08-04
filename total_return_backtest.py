"""Auditable total-return backtest using the private FinMind cache.

No raw market rows are committed.  This module writes only aggregate metrics
and the exact assumptions that produced them.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
BUY_FEE = 0.001425
SELL_FEE = 0.001425
STOCK_SELL_TAX = 0.003
ETF_SELL_TAX = 0.001
SLIPPAGE_BPS = 10
LOOKBACK = 60
HOLDING = 20
PICKS = 5


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def cash_dividends(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Cash dividend per share by ex-dividend date.

    The distribution fields are per-share amounts in the dividend policy
    dataset.  Reinvestment happens at that date's closing price; stock
    dividends are separately counted and reported as a data limitation until
    a licensed adjusted-price feed is available.
    """
    result: dict[str, float] = defaultdict(float)
    for row in rows:
        day = str(row.get("CashExDividendTradingDate") or "")[:10]
        if not day:
            continue
        result[day] += num(row.get("CashEarningsDistribution")) + num(row.get("CashStatutorySurplus"))
    return result


def stock_dividend_events(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if num(row.get("StockEarningsDistribution")) or num(row.get("StockStatutorySurplus"))
    )


@dataclass
class Series:
    code: str
    values: dict[str, float]
    stock_dividend_events: int


def total_return_series(code: str, payload: dict[str, Any]) -> Series:
    prices = sorted(payload.get("TaiwanStockPrice", []), key=lambda row: str(row.get("date", "")))
    dividends = cash_dividends(payload.get("TaiwanStockDividend", []))
    values: dict[str, float] = {}
    previous = 0.0
    wealth = 1.0
    for row in prices:
        day = str(row.get("date", ""))[:10]
        close = num(row.get("close"))
        if not day or close <= 0:
            continue
        if previous > 0:
            price_return = close / previous
            reinvestment = 1.0 + max(0.0, dividends.get(day, 0.0)) / close
            wealth *= price_return * reinvestment
        values[day] = wealth
        previous = close
    return Series(code, values, stock_dividend_events(payload.get("TaiwanStockDividend", [])))


def max_drawdown(returns: list[float]) -> float:
    equity = peak = 1.0
    minimum = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        minimum = min(minimum, equity / peak - 1)
    return minimum


def annualized(total_return: float, days: int) -> float | None:
    if days < 2 or total_return <= -1:
        return None
    return (1 + total_return) ** (252 / days) - 1


def run_period(series: dict[str, Series], dates: list[str], is_etf: bool = False) -> dict[str, Any]:
    returns: list[float] = []
    trades = 0
    for index in range(LOOKBACK, len(dates) - HOLDING, HOLDING):
        signal, entry, exit_ = dates[index], dates[index + 1], dates[index + HOLDING]
        ranked = []
        for code, item in series.items():
            if signal in item.values and dates[index - LOOKBACK] in item.values and entry in item.values and exit_ in item.values:
                momentum = item.values[signal] / item.values[dates[index - LOOKBACK]] - 1
                gross = item.values[exit_] / item.values[entry] - 1
                ranked.append((momentum, gross))
        picks = sorted(ranked, reverse=True)[:PICKS]
        if not picks:
            continue
        gross = sum(item[1] for item in picks) / len(picks)
        slippage = 2 * SLIPPAGE_BPS / 10_000
        sell_tax = ETF_SELL_TAX if is_etf else STOCK_SELL_TAX
        net = (1 + gross) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) * (1 - SELL_FEE - sell_tax - SLIPPAGE_BPS / 10_000) - 1
        returns.append(net)
        trades += len(picks)
    total = math.prod(1 + item for item in returns) - 1 if returns else 0.0
    return {"totalReturn": total, "annualizedReturn": annualized(total, len(dates)), "mdd": max_drawdown(returns), "periods": len(returns), "trades": trades}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "total-return-backtest-status.json")
    args = parser.parse_args()
    stocks = args.cache_dir / "finmind-backtest-v2" / "stocks"
    payloads = {path.stem: load(path) for path in stocks.glob("*.json")}
    if "0050" not in payloads:
        raise SystemExit("benchmark 0050 is not cached")
    universe = {code: total_return_series(code, data) for code, data in payloads.items() if code != "0050"}
    benchmark = total_return_series("0050", payloads["0050"])
    # Do not intersect every constituent's calendar: that would discard the
    # early history merely because a later IPO did not yet exist.  The benchmark
    # calendar is the master calendar; run_period admits a stock only when it
    # has the required lookback, entry and exit observations.
    calendar = sorted(benchmark.values)
    if len(calendar) < 500:
        raise SystemExit("insufficient benchmark history")
    train_end, validation_end = int(len(calendar) * 0.6), int(len(calendar) * 0.8)
    splits = {"train": calendar[:train_end], "validation": calendar[train_end:validation_end], "test": calendar[validation_end:]}
    results = {name: run_period(universe, days) for name, days in splits.items()}
    benchmark_results = {name: run_period({"0050": benchmark}, days, is_etf=True) for name, days in splits.items()}
    passed = (
        results["validation"]["periods"] >= 5
        and results["test"]["periods"] >= 5
        and results["validation"]["totalReturn"] > benchmark_results["validation"]["totalReturn"]
        and results["test"]["totalReturn"] > benchmark_results["test"]["totalReturn"]
    )
    output = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "candidate" if passed else "rejected",
        "universe": {"stocks": len(universe), "benchmark": "0050", "benchmarkTradingDays": len(calendar)},
        "strategy": {"name": "60-day total-return momentum", "lookbackDays": LOOKBACK, "holdingDays": HOLDING, "picks": PICKS},
        "costs": {"buyFee": BUY_FEE, "sellFee": SELL_FEE, "stockSellTax": STOCK_SELL_TAX, "etfSellTax": ETF_SELL_TAX, "oneWaySlippageBps": SLIPPAGE_BPS},
        "dividends": {"cash": "reinvested at ex-dividend date close", "stockDividendEvents": sum(item.stock_dividend_events for item in universe.values()), "limitation": "stock dividend share adjustments require licensed adjusted prices before a strategy can be promoted"},
        "splits": {name: {"strategy": results[name], "benchmark0050": benchmark_results[name]} for name in splits},
        "promotionRule": "Both validation and untouched test must beat 0050 after costs, with at least 5 holding periods each.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
