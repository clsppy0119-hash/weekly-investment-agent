"""The real-rule backtest must degrade honestly, never quietly.

``comprehensive`` puts most of its weight on financials.  Without a
point-in-time fundamentals cache its coverage cannot reach the production
threshold, and the run has to report that it selected nothing rather than
scoring on a thinner basis than the live product would.
"""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from point_in_time_fundamentals import PointInTimeFundamentals
from strategy_backtest import fundamental_records, run_range

# Trailing-twelve-month EPS needs four filed quarters before it exists at all,
# so the fixture carries two years; one year would leave the metric unavailable
# until the very last filing and the style would never unlock.
QUARTERS = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
            "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]


def _history(days=80, codes=("1101", "2330", "2454", "3008")):
    """Rising prices, so every name clears the trend factors."""
    history, dates = [], []
    for index in range(days):
        dates.append(f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}")
        history.append({code: (100.0 + index + offset, 5_000_000.0)
                        for offset, code in enumerate(codes)})
    return history, dates


def _fundamentals_cache(folder: Path, codes):
    stocks = folder / "finmind-fundamentals-v1" / "stocks"
    stocks.mkdir(parents=True)
    for rank, code in enumerate(codes):
        statements, balance = [], []
        for period in QUARTERS:
            statements.append({"date": period, "type": "EPS", "value": 2.0 + rank})
            statements.append({"date": period, "type": "IncomeAfterTaxes", "value": 1e8})
            balance.append({"date": period, "type": "TotalEquity", "value": 1e9})
            balance.append({"date": period, "type": "TotalAssets", "value": 2e9})
            balance.append({"date": period, "type": "TotalLiabilities", "value": 6e8})
        revenue = [{"revenue_year": year, "revenue_month": month, "revenue": 1e8 * (1.2 if year == 2025 else 1.0)}
                   for year in (2024, 2025) for month in range(1, 13)]
        (stocks / f"{code}.json").write_text(json.dumps({
            "TaiwanStockFinancialStatements": statements,
            "TaiwanStockBalanceSheet": balance,
            "TaiwanStockMonthRevenue": revenue,
        }), encoding="utf-8")


def test_comprehensive_selects_nothing_without_fundamentals():
    history, dates = _history()
    result = run_range(history, dates, 0, len(history), "comprehensive",
                       picks=3, holding=5, min_volume=0)

    assert result["trades"] == 0
    assert result["rebalances_without_candidates"] > 0, "the empty runs must be reported"


def test_comprehensive_runs_once_fundamentals_are_available():
    codes = ("1101", "2330", "2454", "3008")
    history, dates = _history(codes=codes)
    with TemporaryDirectory() as folder:
        _fundamentals_cache(Path(folder), codes)
        pit = PointInTimeFundamentals.from_cache(Path(folder))
        result = run_range(history, dates, 0, len(history), "comprehensive",
                           picks=3, holding=5, min_volume=0, pit=pit)

    assert result["trades"] > 0, "published financials must unlock the style"
    assert result["rebalances_without_candidates"] == 0


def test_price_earnings_uses_the_signal_day_price():
    quotes = {"2330": {"price": 600.0}, "1101": {"price": 40.0}}
    published = {"2330": {"eps": 30.0}}
    records = fundamental_records(quotes, published)

    assert records["2330"]["pe"] == 20.0
    assert "pe" not in records["1101"], "no earnings means no P/E, not a guessed one"


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
