"""Backtest a user-specified Taiwan stock basket from the private free-data cache.

This intentionally does not select from the whole market.  A named basket has
no universe reconstruction problem, so it can provide auditable per-stock
total-return evidence while the all-market strategy remains research-only.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from total_return_backtest import (
    BUY_FEE, ETF_SELL_TAX, SELL_FEE, SLIPPAGE_BPS, STOCK_SELL_TAX,
    annualized, load, total_return_series,
)


ROOT = Path(__file__).resolve().parent


def codes_from(raw: str) -> list[str]:
    return sorted({item.strip() for item in raw.split(",") if item.strip().isdigit()})


def first_last_common(series: dict[str, float], benchmark: dict[str, float]) -> tuple[str, str] | None:
    dates = sorted(set(series) & set(benchmark))
    return (dates[0], dates[-1]) if len(dates) >= 2 else None


def net_return(item: Any, benchmark: Any, is_etf: bool = False) -> dict[str, Any] | None:
    period = first_last_common(item.values, benchmark.values)
    if not period:
        return None
    start, end = period
    gross_factor = item.values[end] / item.values[start]
    entry_factor = 1 - BUY_FEE - SLIPPAGE_BPS / 10_000
    exit_factor = 1 - SELL_FEE - (ETF_SELL_TAX if is_etf else STOCK_SELL_TAX) - SLIPPAGE_BPS / 10_000
    total = gross_factor * entry_factor * exit_factor - 1
    days = max(1, (datetime.fromisoformat(end).date() - datetime.fromisoformat(start).date()).days)
    benchmark_return = benchmark.values[end] / benchmark.values[start] - 1
    return {
        "from": start, "to": end, "calendarDays": days,
        "grossTotalReturn": gross_factor - 1,
        "netTotalReturn": total,
        "annualizedNetReturn": annualized(total, days),
        "benchmark0050GrossReturn": benchmark_return,
        "excessVs0050Gross": gross_factor - 1 - benchmark_return,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Auditable fixed-basket total-return report")
    parser.add_argument("--codes", default=os.environ.get("FIXED_UNIVERSE_CODES", ""), help="Comma-separated explicit stock codes")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "fixed-universe-backtest-status.json")
    args = parser.parse_args()
    requested = codes_from(args.codes)
    if not requested:
        result = {
            "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(), "status": "not_configured",
            "scope": "Set GitHub Actions variable FIXED_UNIVERSE_CODES to a comma-separated self-selected basket.",
            "promotion": "This report is research evidence only, not an all-market selection strategy.",
        }
    else:
        stocks = args.cache_dir / "finmind-backtest-v2" / "stocks"
        payloads = {code: load(stocks / f"{code}.json") for code in requested + ["0050"] if (stocks / f"{code}.json").exists()}
        missing = sorted(set(requested) - set(payloads))
        if "0050" not in payloads:
            raise SystemExit("benchmark 0050 is not cached")
        benchmark = total_return_series("0050", payloads["0050"])
        reports = {}
        for code in requested:
            if code in payloads:
                metrics = net_return(total_return_series(code, payloads[code]), benchmark)
                reports[code] = metrics or {"error": "no common price dates with 0050"}
        result = {
            "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(), "status": "complete",
            "scope": "Explicit user fixed basket only; it is not a full-market selection or ranking backtest.",
            "requestedCodes": requested, "missingCachedCodes": missing, "reports": reports,
            "method": {
                "totalReturn": "raw close, cash dividends reinvested on ex-date close, stock dividends as share-count factors",
                "costs": {"buyFee": BUY_FEE, "sellFee": SELL_FEE, "stockSellTax": STOCK_SELL_TAX, "etfSellTax": ETF_SELL_TAX, "oneWaySlippageBps": SLIPPAGE_BPS},
                "benchmark": "0050 gross total return over each stock's common available dates",
            },
            "limitations": ["Results cover only the supplied fixed basket.", "No claim is made about stocks not supplied.", "Free provider availability and corporate-action records should be reviewed before investment decisions."],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
