"""Backtest the ranking rule the product actually ships.

``backtest.py`` validates a plain price-momentum rule.  The daily report ranks
candidates with the weighted multi-factor score in ``scoring``.  Those are
different strategies, so a passing momentum backtest said nothing about the
recommendations users see.  This runs the real rule.

Factors are rebuilt from official TWSE closes at each signal date, so a style
whose weights are mostly technical (``swing``) can be measured today.  Styles
that lean on financials (``comprehensive``, ``value``, ``dividend``) need a
point-in-time fundamentals series; without one their coverage stays below the
production threshold and the run reports zero candidates rather than quietly
scoring on a thinner basis than production would.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path
from random import Random
from statistics import mean, stdev

from backtest import (
    BUY_FEE, DEFAULT_BENCHMARK, DEFAULT_DATA, ETF_SELL_TAX, SELL_FEE,
    SLIPPAGE_BPS, STOCK_SELL_TAX, benchmark_total_return, load_history,
)
from point_in_time_fundamentals import PointInTimeFundamentals
from scoring import MINIMUM_COVERAGE, WEIGHTS, candidates

MA_LONG = 20


def factor_quotes(history: list[dict], index: int, min_volume: float) -> dict[str, dict]:
    """Rebuild price, ma5, ma20 and change as of ``index``, using no later day."""
    today = history[index]
    quotes: dict[str, dict] = {}
    for code, (price, volume) in today.items():
        if volume < min_volume:
            continue
        closes = [history[index - back].get(code) for back in range(MA_LONG)]
        closes = [item[0] for item in closes if item]
        if len(closes) < MA_LONG:
            continue
        previous = history[index - 1].get(code)
        quotes[code] = {
            "price": price,
            "volume": volume,
            "ma5": mean(closes[:5]),
            "ma20": mean(closes),
            "change": (price / previous[0] - 1) * 100 if previous else None,
        }
    return quotes


def benchmark_series(path: Path) -> dict[str, float]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    return {str(row.get("date")): row["total_return"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("total_return"), (int, float))}


def benchmark_between(series: dict[str, float], entry: str, exit_: str) -> float | None:
    """Net 0050 return over one holding period, on ETF costs."""
    start, end = series.get(entry), series.get(exit_)
    if not start or end is None or start <= 0:
        return None
    gross = end / start - 1
    return (1 + gross) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) * (1 - SELL_FEE - ETF_SELL_TAX - SLIPPAGE_BPS / 10_000) - 1


def significance(excess: list[float], resamples: int = 2000, seed: int = 20260808) -> dict:
    """Is the per-rebalance edge distinguishable from luck?

    A cumulative return says nothing about reliability: nine rebalances can
    compound to a large number on one lucky pick.  The t statistic and the
    bootstrap interval are what decide whether an edge is real, and a interval
    spanning zero means the variant has not been shown to work.
    """
    count = len(excess)
    if count < 3:
        return {"rebalances": count, "conclusive": False, "reason": "fewer_than_three_rebalances"}
    average = mean(excess)
    spread = stdev(excess)
    t_stat = average / (spread / sqrt(count)) if spread else None
    generator = Random(seed)
    means = []
    for _ in range(resamples):
        sample = [excess[generator.randrange(count)] for _ in range(count)]
        means.append(mean(sample))
    means.sort()
    low = means[int(0.025 * resamples)]
    high = means[int(0.975 * resamples) - 1]
    return {
        "rebalances": count,
        "meanExcessPerRebalance": average,
        "stdev": spread,
        "tStat": t_stat,
        "ci95": [low, high],
        # An interval containing zero cannot support a promotion decision.
        "conclusive": low > 0 or high < 0,
    }


def fundamental_records(quotes: dict[str, dict], published: dict[str, dict]) -> dict[str, dict]:
    """Merge published financials with the ratios that need a live price.

    Only companies with a published record carry financial factors; the rest keep
    an empty record so they still enter ranking on technicals alone, exactly as
    production behaves when its fundamentals cache is incomplete.
    """
    records = {}
    for code, quote in quotes.items():
        record = dict(published.get(code, {}))
        eps = record.get("eps")
        if eps and eps > 0 and quote.get("price"):
            record["pe"] = quote["price"] / eps
        records[code] = record
    return records


def run_range(history: list[dict], dates: list[str], start: int, end: int, style: str, picks: int,
              holding: int, min_volume: float, weights: dict | None = None,
              minimum_coverage: int | None = None, pit: object | None = None,
              continuous_trend: bool = False) -> dict:
    """Signals inside ``[start, end)``, exits kept inside it too.

    Moving averages read days before ``start`` on purpose: that is past data at
    signal time, and restarting the warm-up per split would throw away the first
    twenty days of every window.  Exits are held below ``end`` so a position
    never reaches into the next split.
    """
    returns: list[float] = []
    rebalances: list[dict] = []
    wins = 0
    unfilled = 0
    stale_exits = 0
    no_candidate = 0
    index = max(start, MA_LONG)
    while index + holding + 1 < end:
        quotes = factor_quotes(history, index, min_volume)
        # Fundamentals are read as of the signal date, so only figures already
        # filed on that day can influence the pick.  Without a cache every name
        # carries an empty record and `coverage` reflects that honestly.
        published = pit.as_of(dates[index]) if pit is not None else {}
        selected = candidates(style, quotes, fundamental_records(quotes, published), picks,
                              weights=weights, minimum_coverage=minimum_coverage,
                              continuous_trend=continuous_trend)
        window = history[index + 1:index + holding + 2]
        trade_returns: list[float] = []
        for _score, _coverage, code, _quote, _fund in selected:
            entry = window[0].get(code)
            if not entry:
                unfilled += 1
                continue
            exit_price = window[-1].get(code)
            if not exit_price:
                exit_price = next((day[code] for day in reversed(window[:-1]) if code in day), None)
                stale_exits += 1
            if not exit_price:
                continue
            trade_returns.append(exit_price[0] / entry[0] - 1)
        if not selected:
            no_candidate += 1
        if trade_returns:
            gross = mean(trade_returns)
            net = (1 + gross) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) * (1 - SELL_FEE - STOCK_SELL_TAX - SLIPPAGE_BPS / 10_000) - 1
            returns.append(net)
            # Kept per rebalance so the split's edge can be tested for
            # significance instead of resting on one cumulative number.
            rebalances.append({"entry": dates[index + 1], "exit": dates[index + holding + 1], "net": net})
            wins += net > 0
        index += holding
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
    return {
        "return": equity - 1, "mdd": drawdown, "trades": len(returns),
        "win_rate": wins / len(returns) if returns else 0.0,
        "unfilled": unfilled, "stale_exits": stale_exits, "rebalances_without_candidates": no_candidate,
        "returns": returns, "rebalances": rebalances,
    }


def evaluate(data: Path, benchmark: Path, style: str, picks: int, holding: int,
             min_volume: float, drop: tuple[str, ...] = (), pit: object | None = None,
             continuous_trend: bool = False) -> dict:
    dates, history = load_history(data)
    if len(history) < 120:
        raise SystemExit(f"歷史資料只有 {len(history)} 個交易日，不足 120，無法切出樣本外測試。")
    train_end = int(len(history) * 0.60)
    validation_end = int(len(history) * 0.80)
    parts = {
        "train": (0, train_end),
        "validation": (train_end, validation_end),
        "test": (validation_end, len(history)),
    }
    weights = {key: value for key, value in WEIGHTS[style].items() if key not in drop} if drop else None
    # Dropping factors lowers the achievable coverage, so the production floor
    # would reject every candidate; scale it by what is still reachable.
    coverage_floor = None
    if weights is not None:
        reachable = sum(weights.get(key, 0) for key in ("trend20", "trend5", "change"))
        coverage_floor = min(MINIMUM_COVERAGE[style], reachable)
    series = benchmark_series(benchmark)
    result = {}
    for name, (start, end) in parts.items():
        run = run_range(history, dates, start, end, style, picks, holding, min_volume,
                        weights, coverage_floor, pit, continuous_trend)
        run["benchmark"] = benchmark_total_return(benchmark, dates[start:end])
        run["excess"] = None if run["benchmark"] is None else run["return"] - run["benchmark"]
        per_rebalance = []
        for item in run["rebalances"]:
            reference = benchmark_between(series, item["entry"], item["exit"])
            if reference is not None:
                per_rebalance.append(item["net"] - reference)
        run["significance"] = significance(per_rebalance)
        run.pop("returns")
        run.pop("rebalances")
        result[name] = run
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "rule": "production scoring rule from scoring.py",
        "style": style,
        "parameters": {"picks": picks, "holding": holding, "minVolume": min_volume,
                       "minimumCoverage": MINIMUM_COVERAGE[style]},
        "dataStart": dates[0], "dataEnd": dates[-1], "tradingDays": len(history),
        "factorBasis": ("technical factors plus point-in-time fundamentals" if pit is not None
                        else "technical only (no point-in-time fundamentals available)"),
        "splits": result,
        "status": "research_only",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="以正式評分規則回測")
    parser.add_argument("--input", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--style", default="swing", choices=sorted(MINIMUM_COVERAGE))
    parser.add_argument("--picks", type=int, default=3)
    parser.add_argument("--holding", type=int, default=5)
    parser.add_argument("--min-volume", type=float, default=500_000)
    parser.add_argument("--drop", action="append", default=[],
                        help="研究用：從權重中移除某個因子，可重複（例如 --drop change）")
    parser.add_argument("--continuous-trend", action="store_true",
                        help="研究用：趨勢因子改用與均線的乖離幅度，取代二元的 75/35")
    parser.add_argument("--fundamentals-cache", type=Path, default=None,
                        help="私有歷史財報快取目錄；提供後才能回測依賴基本面的 style")
    parser.add_argument("--output", type=Path, default=Path("data/strategy-backtest.json"))
    args = parser.parse_args()
    pit = PointInTimeFundamentals.from_cache(args.fundamentals_cache) if args.fundamentals_cache else None
    result = evaluate(args.input, args.benchmark, args.style, args.picks, args.holding,
                      args.min_volume, tuple(args.drop), pit, args.continuous_trend)
    result["droppedFactors"] = list(args.drop)
    result["continuousTrend"] = args.continuous_trend
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
