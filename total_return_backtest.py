"""Auditable total-return backtest using the private FinMind cache.

No raw market rows are committed.  This module writes only aggregate metrics
and the exact assumptions that produced them.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from statistics import pstdev
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from execution_accounting import (
    aggregate_periods,
    daily_equity_curve,
    max_drawdown_from_equity,
    max_drawdown_from_period_returns,
    rebalance_schedule,
    settle_equal_weight_period,
    unconfigured_risk_policy,
)
from market_membership_snapshots import SNAPSHOT_DIR_NAME, load_membership


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
    return json.loads(path.read_text(encoding="utf-8-sig"))


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


def stock_dividend_factors(rows: list[dict[str, Any]]) -> dict[str, float]:
    factors: dict[str, float] = defaultdict(lambda: 1.0)
    for row in rows:
        day = str(row.get("StockExDividendTradingDate") or "")[:10]
        stock = num(row.get("StockEarningsDistribution")) + num(row.get("StockStatutorySurplus"))
        if day and stock > 0:
            # Stock dividend is stated in nominal dollars per share; NT$10
            # corresponds to one additional share.
            factors[day] *= 1 + stock / 10
    return factors


def stock_dividend_validation(payload: dict[str, Any]) -> tuple[int, int, float | None]:
    policies = {str(row.get("StockExDividendTradingDate") or "")[:10]: row for row in payload.get("TaiwanStockDividend", [])}
    errors: list[float] = []
    events = 0
    for action in payload.get("TaiwanStockDividendResult", []):
        row = policies.get(str(action.get("date") or "")[:10])
        if not row:
            continue
        stock = num(row.get("StockEarningsDistribution")) + num(row.get("StockStatutorySurplus"))
        if stock <= 0:
            continue
        events += 1
        before, after = num(action.get("before_price")), num(action.get("after_price"))
        cash = num(row.get("CashEarningsDistribution")) + num(row.get("CashStatutorySurplus"))
        expected_after = max(0.0, before - cash) / (1 + stock / 10)
        if before > 0 and after > 0 and expected_after > 0:
            errors.append(abs(after / expected_after - 1))
    return events, len(errors), (sum(errors) / len(errors) if errors else None)


@dataclass
class Series:
    code: str
    values: dict[str, float]
    stock_dividend_events: int
    entry_date: str | None = None
    exit_date: str | None = None


def total_return_series(code: str, payload: dict[str, Any], entry_date: str | None = None,
                        exit_date: str | None = None) -> Series:
    prices = sorted(payload.get("TaiwanStockPrice", []), key=lambda row: str(row.get("date", "")))
    dividends = cash_dividends(payload.get("TaiwanStockDividend", []))
    stock_factors = stock_dividend_factors(payload.get("TaiwanStockDividend", []))
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
            wealth *= price_return * reinvestment * stock_factors.get(day, 1.0)
        values[day] = wealth
        previous = close
    return Series(code, values, stock_dividend_events(payload.get("TaiwanStockDividend", [])), entry_date, exit_date)


def official_benchmark_series(path: Path) -> Series:
    """Load the TWSE Taiwan 50 total-return index as the benchmark.

    This avoids treating the 0050 ETF's split-affected raw price history as
    an index benchmark.  The source file is generated by the official TWSE
    TAI50I endpoint and contains ``date``/``total_return`` rows.
    """
    rows = load(path)
    values = {
        str(row.get("date"))[:10]: num(row.get("total_return"))
        for row in rows
        if isinstance(row, dict) and row.get("date") and num(row.get("total_return")) > 0
    }
    if len(values) < 20:
        raise SystemExit("official Taiwan 50 total-return benchmark is missing or too short")
    base = values[min(values)]
    values = {day: value / base for day, value in values.items()}
    return Series("0050_TR", values, 0)


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


def run_period(series: dict[str, Series], dates: list[str], is_etf: bool = False,
               lookback: int = LOOKBACK, holding: int = HOLDING, picks_count: int = PICKS,
               ranking_mode: str = "momentum",
               membership_by_date: dict[str, set[str]] | None = None) -> dict[str, Any]:
    periods: list[dict[str, Any]] = []
    schedule = rebalance_schedule(
        len(dates), lookback, holding, convention="signal_plus_holding"
    )
    sell_tax = ETF_SELL_TAX if is_etf else STOCK_SELL_TAX
    buy_cost = BUY_FEE + SLIPPAGE_BPS / 10_000
    sell_cost = SELL_FEE + sell_tax + SLIPPAGE_BPS / 10_000
    for point in schedule:
        index = point.signal_index
        signal, entry, exit_ = dates[index], dates[point.entry_index], dates[point.exit_index]
        path_days = dates[point.entry_index:point.exit_index + 1]
        ranked = []
        for code, item in series.items():
            if membership_by_date is not None and code not in membership_by_date.get(signal, set()):
                continue
            if item.entry_date and signal < item.entry_date:
                continue
            # Only signal-day knowledge may exclude a candidate.  Screening on
            # the delisting date, the entry price, or the exit price would drop
            # exactly the positions that lose money during the holding period.
            if item.exit_date and signal >= item.exit_date:
                continue
            if signal not in item.values or dates[index - lookback] not in item.values:
                continue
            momentum = item.values[signal] / item.values[dates[index - lookback]] - 1
            if ranking_mode == "risk_adjusted":
                trail = [item.values[day] for day in dates[max(0, index - 20):index + 1] if day in item.values]
                if len(trail) < 10:
                    continue
                daily = [trail[pos] / trail[pos - 1] - 1 for pos in range(1, len(trail))]
                momentum /= max(pstdev(daily), 0.01)
            ranked.append((momentum, code))
        ranked_order = sorted(ranked, reverse=True)
        selected = ranked_order[:picks_count]
        tie_dependent = 0
        if len(ranked_order) > picks_count and selected and ranked_order[picks_count][0] == selected[-1][0]:
            cutoff = selected[-1][0]
            tie_dependent = sum(1 for score, _ in selected if score == cutoff)
        outcomes: list[dict[str, Any]] = []
        for _, code in selected:
            item = series[code]
            limit = item.exit_date
            if limit and entry >= limit:
                outcomes.append({"status": "cash_unfilled", "pathLength": len(path_days)})
                continue
            if entry not in item.values:
                outcomes.append({"status": "cash_unfilled", "pathLength": len(path_days)})
                continue
            missing = [day for day in path_days if day not in item.values]
            reason = None
            if limit and exit_ >= limit:
                reason = "official_terminal_value_missing"
            elif exit_ not in item.values:
                reason = "nominal_exit_missing"
            elif missing:
                reason = "daily_mark_missing"
            if reason:
                outcomes.append({"status": "unresolved_exit", "reason": reason, "pathLength": len(path_days)})
                continue
            entry_value = item.values[entry]
            factors = [item.values[day] / entry_value for day in path_days]
            outcomes.append({
                "status": "closed",
                "grossReturn": factors[-1] - 1,
                "dailyGrossFactors": factors,
            })
        period = settle_equal_weight_period(
            outcomes,
            picks_count,
            buy_cost=buy_cost,
            sell_cost=sell_cost,
        )
        period["tieBreakDependentSlots"] = tie_dependent
        if tie_dependent:
            period["blockers"] = sorted(set(period["blockers"]) | {"cutoff_tie_dependent"})
        period.update({"signalDate": signal, "entryDate": entry, "exitDate": exit_})
        periods.append(period)

    comparison_from = dates[schedule[0].entry_index] if schedule else None
    comparison_to = dates[schedule[-1].exit_index] if schedule else None
    accounting = aggregate_periods(periods, comparison_from=comparison_from, comparison_to=comparison_to)
    comparison_days = (
        dates.index(comparison_to) - dates.index(comparison_from) + 1
        if comparison_from and comparison_to else 0
    )
    accounting["comparisonTradingDays"] = comparison_days
    selection_certified = accounting["tieBreakDependentSlots"] == 0
    certified_returns = [period["return"] for period in periods] if accounting["complete"] else None
    total = math.prod(1 + value for value in certified_returns) - 1 if certified_returns is not None else None
    curve = daily_equity_curve(periods)
    mdd = max_drawdown_from_equity(curve)
    result = {
        "totalReturn": total,
        "annualizedReturn": annualized(total, comparison_days) if total is not None else None,
        "mdd": mdd,
        "mddBasis": "daily_mark_to_market_including_costs",
        "selectionMdd": max_drawdown_from_period_returns(certified_returns),
        "selectionCertified": selection_certified,
        "periods": accounting["investedPeriods"],
        "scheduledPeriods": accounting["scheduledPeriods"],
        "trades": accounting["closedSlots"],
        "executionComplete": accounting["complete"],
        "executionAccounting": accounting,
    }
    result.update(unconfigured_risk_policy())
    return result


def buy_and_hold_metrics(series: Series, dates: list[str], comparison_from: str | None,
                         comparison_to: str | None, *, is_etf: bool = True) -> dict[str, Any]:
    """One exact-boundary round trip for the benchmark comparison."""
    sell_tax = ETF_SELL_TAX if is_etf else STOCK_SELL_TAX
    buy_cost = BUY_FEE + SLIPPAGE_BPS / 10_000
    sell_cost = SELL_FEE + sell_tax + SLIPPAGE_BPS / 10_000
    if not comparison_from or not comparison_to or comparison_from not in dates or comparison_to not in dates:
        path_days: list[str] = []
    else:
        start, end = dates.index(comparison_from), dates.index(comparison_to)
        path_days = dates[start:end + 1] if start < end else []
    missing = [day for day in path_days if day not in series.values]
    if len(path_days) < 2 or missing:
        period = settle_equal_weight_period(
            [{"status": "unresolved_exit", "reason": "benchmark_exact_path_missing", "pathLength": max(2, len(path_days))}],
            1,
            buy_cost=buy_cost,
            sell_cost=sell_cost,
        )
    else:
        entry_value = series.values[comparison_from]
        factors = [series.values[day] / entry_value for day in path_days]
        period = settle_equal_weight_period(
            [{"status": "closed", "grossReturn": factors[-1] - 1, "dailyGrossFactors": factors}],
            1,
            buy_cost=buy_cost,
            sell_cost=sell_cost,
        )
    accounting = aggregate_periods([period], comparison_from=comparison_from, comparison_to=comparison_to)
    accounting["comparisonTradingDays"] = len(path_days)
    total = period["return"] if accounting["complete"] else None
    result = {
        "totalReturn": total,
        "annualizedReturn": annualized(total, len(path_days)) if total is not None else None,
        "mdd": max_drawdown_from_equity(daily_equity_curve([period])),
        "mddBasis": "daily_mark_to_market_including_costs",
        "periods": 1 if accounting["complete"] else 0,
        "scheduledPeriods": 1,
        "trades": accounting["closedSlots"],
        "executionComplete": accounting["complete"],
        "executionAccounting": accounting,
    }
    result.update(unconfigured_risk_policy())
    return result


def research_universe_codes(payload_codes: set[str], evidence: dict[str, Any],
                            membership_by_date: dict[str, set[str]], fixed_codes: set[str]) -> set[str]:
    """Choose candidates from direct daily evidence, with listing dates as fallback."""
    snapshot_codes = set().union(*membership_by_date.values()) if membership_by_date else set()
    verified_entry_codes = {
        code for code, item in evidence.items()
        if isinstance(item, dict) and item.get("entryDate")
    }
    evidence_codes = snapshot_codes if snapshot_codes else verified_entry_codes
    return (payload_codes - {"0050"}) & (evidence_codes | fixed_codes)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "total-return-backtest-status.json")
    parser.add_argument("--benchmark-file", type=Path, default=None,
                        help="official TWSE Taiwan 50 total-return JSON")
    args = parser.parse_args()
    stocks = args.cache_dir / "finmind-backtest-v2" / "stocks"
    payloads = {path.stem: load(path) for path in stocks.glob("*.json")}
    official_dir = args.cache_dir / "official-listing-history-v1"
    certification_path = official_dir / "universe-certification.json"
    universe_status = load(certification_path) if certification_path.exists() else {}
    snapshot_dir = args.cache_dir / SNAPSHOT_DIR_NAME
    snapshot_status_path = ROOT / "data" / "market-membership-snapshot-status.json"
    snapshot_status = load(snapshot_status_path) if snapshot_status_path.exists() else {}
    membership_by_date = load_membership(snapshot_dir)
    evidence_path = official_dir / "semiconductor-membership-evidence.json"
    evidence = load(evidence_path) if evidence_path.exists() else {}
    if "0050" not in payloads:
        raise SystemExit("benchmark 0050 is not cached")
    # The research run itself follows the same strict inclusion rule as the
    # promotion gate: no official entry-date evidence, no stock in the run.
    fixed_codes = {
        item.strip() for item in os.environ.get("FIXED_UNIVERSE_CODES", "").split(",")
        if item.strip().isdigit() and len(item.strip()) == 4
    }
    research_codes = research_universe_codes(set(payloads), evidence, membership_by_date, fixed_codes)
    universe = {
        code: total_return_series(
            code,
            data,
            evidence.get(code, {}).get("entryDate"),
            evidence.get(code, {}).get("exitDate"),
        )
        for code, data in payloads.items()
        if code in research_codes
    }
    if not universe:
        raise SystemExit("no officially verifiable stocks available for strict research backtest")
    benchmark = official_benchmark_series(args.benchmark_file) if args.benchmark_file else total_return_series("0050", payloads["0050"])
    # Do not intersect every constituent's calendar: that would discard the
    # early history merely because a later IPO did not yet exist.  The benchmark
    # calendar is the master calendar; run_period admits a stock only when it
    # has the required lookback, entry and exit observations.
    calendar = sorted(benchmark.values)
    if len(calendar) < 100:
        raise SystemExit("insufficient benchmark history")
    train_end, validation_end = int(len(calendar) * 0.6), int(len(calendar) * 0.8)
    splits = {"train": calendar[:train_end], "validation": calendar[train_end:validation_end], "test": calendar[validation_end:]}
    results = {name: run_period(universe, days, membership_by_date=membership_by_date) for name, days in splits.items()}
    benchmark_results = {
        name: buy_and_hold_metrics(
            benchmark,
            days,
            results[name]["executionAccounting"]["comparisonFrom"],
            results[name]["executionAccounting"]["comparisonTo"],
            is_etf=True,
        )
        for name, days in splits.items()
    }
    execution_complete = all(
        results[name]["executionComplete"] and benchmark_results[name]["executionComplete"]
        for name in ("validation", "test")
    )
    selection_certified = all(results[name]["selectionCertified"] for name in ("validation", "test"))
    risk_gate_passed = all(results[name]["dailyMddGatePassed"] for name in ("validation", "test"))
    performance_passed = (
        execution_complete
        and selection_certified
        and risk_gate_passed
        and results["validation"]["periods"] >= 5
        and results["test"]["periods"] >= 5
        and results["validation"]["totalReturn"] > benchmark_results["validation"]["totalReturn"]
        and results["test"]["totalReturn"] > benchmark_results["test"]["totalReturn"]
    )
    validations = [stock_dividend_validation(data) for code, data in payloads.items() if code != "0050"]
    stock_events = sum(item[0] for item in validations)
    stock_matches = sum(item[1] for item in validations)
    total_error = sum((item[2] or 0.0) * item[1] for item in validations)
    stock_error = total_error / stock_matches if stock_matches else None
    # Never promote a result that has not modelled the share-count adjustment
    # for stock dividends.  Cash dividends are already reinvested, but using
    # this result as a buy signal would otherwise overstate confidence.
    stock_adjustment_validated = stock_matches >= 20 and stock_error is not None and stock_error <= 0.03
    # The current cache universe is assembled from companies that pass today's
    # completeness gate.  Do not promote it until historical membership is
    # reconstructed at each rebalance date, otherwise performance can contain
    # survivorship bias.
    # This is intentionally derived only from the strict official verifier;
    # price history or today's membership must never open this gate.
    point_in_time_universe = bool(snapshot_status.get("certified", False))
    promotion_blocked = (
        not execution_complete
        or not selection_certified
        or not risk_gate_passed
        or not stock_adjustment_validated
        or not point_in_time_universe
        or len(calendar) < 500
        or bool(fixed_codes)
    )
    output = {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "research_only" if (not execution_complete or promotion_blocked) else ("candidate" if performance_passed else "rejected"),
        "universe": {"stocks": len(universe), "benchmark": "0050", "benchmarkTradingDays": len(calendar), "pointInTimeMembership": point_in_time_universe, "inclusionRule": "explicit fixed basket" if fixed_codes else "official TWSE/TPEx daily membership snapshots"},
        "strategy": {"name": "60-day total-return momentum", "lookbackDays": LOOKBACK, "holdingDays": HOLDING, "picks": PICKS},
        "costs": {"buyFee": BUY_FEE, "sellFee": SELL_FEE, "stockSellTax": STOCK_SELL_TAX, "etfSellTax": ETF_SELL_TAX, "oneWaySlippageBps": SLIPPAGE_BPS},
        "dividends": {"cash": "reinvested at ex-dividend date close", "stockDividendEvents": stock_events, "stockDividendMatchedEvents": stock_matches, "stockDividendReferencePriceError": stock_error, "stock": "share count adjusted using validated ex-right reference-price mapping"},
        "splits": {name: {"strategy": results[name], "benchmark0050": benchmark_results[name]} for name in splits},
        "promotionRule": "Both validation and untouched test must have certified execution, beat one exact-boundary 0050 buy-and-hold round trip after costs, and contain at least 5 invested holding periods each.",
        "promotionBlocked": promotion_blocked,
        "promotionBlockers": [
            blocker for blocker, active in {
                "execution_accounting_incomplete": not execution_complete,
                "cutoff_tie_dependent": not selection_certified,
                "daily_mdd_limit_unconfigured": not risk_gate_passed,
                "stock_dividend_adjustment": not stock_adjustment_validated,
                "survivorship_bias": not point_in_time_universe,
                "fixed_universe_only": bool(fixed_codes),
                "short_history": len(calendar) < 500,
            }.items() if active
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
