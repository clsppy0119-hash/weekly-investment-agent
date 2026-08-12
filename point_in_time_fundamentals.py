"""Turn cached FinMind statements into a point-in-time fundamentals series.

``finmind_fundamental_cache.py`` already stores the raw statements.  A backtest
cannot read them directly: a Q2 statement describes 30 June but is not public
until mid-August, so scoring a June signal with it is look-ahead of the worst
kind -- it hands the strategy earnings nobody could have known.

Every figure here is therefore stamped with the date it became *knowable*, and
``as_of`` only ever returns figures already published on that date.

Field names are declared in FIELDS below.  Run ``--inspect`` against a real
cache to print the ``type`` values actually present before trusting a mapping.
"""

from __future__ import annotations

import argparse
import json
import os
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CACHE_SUBDIR = "finmind-fundamentals-v1"

# FinMind reports statements in long form: one row per (date, type, value).
FIELDS = {
    "eps": ("EPS",),
    "net_income": ("IncomeAfterTaxes", "ProfitLoss", "NetIncome"),
    "equity": ("TotalEquity", "Equity", "StockholdersEquity"),
    "assets": ("TotalAssets",),
    "liabilities": ("TotalLiabilities", "Liabilities"),
}

# Statutory Taiwan filing deadlines; a figure is unusable before these.
QUARTER_PUBLICATION = {3: (5, 15), 6: (8, 14), 9: (11, 14), 12: (3, 31)}
MONTHLY_REVENUE_DAY = 10


def iso(value: Any) -> str:
    return str(value or "")[:10]


QUARTER_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def period_end(value: Any) -> str | None:
    """Normalise a reported financial period to the date it closed.

    The pipeline carries three forms for the same thing -- ``2026-06-30``,
    ``2026Q2`` and the ROC ``115Q2`` -- and the filing deadline can only be
    derived from the period's end.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None
    head, _, quarter = text.partition("Q")
    if not (head.isdigit() and quarter[:1].isdigit()):
        return None
    year = int(head)
    if year < 1911:  # ROC year
        year += 1911
    end = QUARTER_END.get(int(quarter[:1]))
    return f"{year}-{end}" if end else None


def quarter_publication(period_end: str) -> str:
    """Date a quarterly statement becomes public, per TWSE filing deadlines."""
    end = date.fromisoformat(period_end)
    month, day = QUARTER_PUBLICATION.get(end.month, (end.month, 28))
    year = end.year + 1 if end.month == 12 else end.year
    return date(year, month, day).isoformat()


def revenue_publication(year: int, month: int) -> str:
    """Monthly revenue is filed by the 10th of the following month."""
    following = date(year, month, 28) + timedelta(days=7)
    return date(following.year, following.month, MONTHLY_REVENUE_DAY).isoformat()


def _revisions_by_type(
    rows: list[dict], names: tuple[str, ...]
) -> list[tuple[date, int, str, float, bool]]:
    """Bind each value revision to its own availability day.

    Legacy rows without an explicit timestamp retain the statutory filing-day
    fallback for ratio calculation, but they cannot count toward certified
    multi-year history.  A late corrected row therefore cannot borrow the
    original row's earlier availability.
    """
    wanted = {name.lower() for name in names}
    result: list[tuple[date, int, str, float, bool]] = []
    for order, row in enumerate(rows):
        if str(row.get("type", "")).lower() not in wanted:
            continue
        period = period_end(row.get("date"))
        if period is None:
            continue
        explicit = False
        available = None
        raw_available = row.get("availableAt")
        if isinstance(raw_available, str):
            try:
                parsed = datetime.fromisoformat(raw_available.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None:
                available = parsed.astimezone(timezone(timedelta(hours=8))).date()
                explicit = True
        if available is None:
            available = date.fromisoformat(quarter_publication(period))
        try:
            number = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        result.append((available, order, period, number, explicit))
    result.sort(key=lambda item: (item[0], item[1], item[2]))
    return result


def _values_as_of(
    revisions: list[tuple[date, int, str, float, bool]], as_of: date
) -> dict[str, float]:
    """Select the latest available revision for each reported period."""
    selected: dict[str, tuple[date, int, float]] = {}
    for available, order, period, value, _explicit in revisions:
        if available <= as_of:
            previous = selected.get(period)
            if previous is None or (available, order) > previous[:2]:
                selected[period] = (available, order, value)
    return {period: item[2] for period, item in selected.items()}


def _explicit_first_availability(
    revisions: list[tuple[date, int, str, float, bool]]
) -> dict[str, date]:
    result: dict[str, date] = {}
    for available, _order, period, _value, explicit in revisions:
        if explicit and (period not in result or available < result[period]):
            result[period] = available
    return result


def _complete_financial_years_at(
    availability: dict[str, dict[str, date]], as_of: date
) -> int:
    """Count full years whose five required statement fields were all available.

    One quarter or one metric never counts as a year.  Rows without their own
    timezone-bearing ``availableAt`` are deliberately ineligible for this
    history gate, so a modern cache cannot silently backfill an old signal.
    """
    years = {
        int(period[:4])
        for series in availability.values()
        for period in series
        if len(period) >= 4 and period[:4].isdigit()
    }
    complete = 0
    for year in years:
        quarters = tuple(f"{year}-{suffix}" for suffix in ("03-31", "06-30", "09-30", "12-31"))
        if all(
            period in availability[field] and availability[field][period] <= as_of
            for field in FIELDS
            for period in quarters
        ):
            complete += 1
    return complete


def _ttm(series: dict[str, float], period: str, quarters: int = 4) -> float | None:
    """Sum of the last ``quarters`` reported periods up to and including ``period``."""
    periods = sorted(key for key in series if key <= period)
    if len(periods) < quarters:
        return None
    return sum(series[key] for key in periods[-quarters:])


def build_stock(payload: dict[str, Any]) -> list[tuple[str, dict[str, float]]]:
    """``[(knowable_from, metrics), ...]`` ascending, for one company."""
    statements = payload.get("TaiwanStockFinancialStatements", [])
    balance = payload.get("TaiwanStockBalanceSheet", [])
    revenue_rows = payload.get("TaiwanStockMonthRevenue", [])

    revisions = {
        "eps": _revisions_by_type(statements, FIELDS["eps"]),
        "net_income": _revisions_by_type(statements, FIELDS["net_income"]),
        "equity": _revisions_by_type(balance, FIELDS["equity"]),
        "assets": _revisions_by_type(balance, FIELDS["assets"]),
        "liabilities": _revisions_by_type(balance, FIELDS["liabilities"]),
    }
    history_availability = {
        field: _explicit_first_availability(series)
        for field, series in revisions.items()
    }

    events: dict[str, dict[str, float]] = defaultdict(dict)

    for available_day in sorted({item[0] for series in revisions.values() for item in series}):
        available = available_day.isoformat()
        entry = events[available]
        entry["financialHistoryYears"] = _complete_financial_years_at(
            history_availability, available_day
        )
        values = {
            field: _values_as_of(series, available_day)
            for field, series in revisions.items()
        }
        eps, net_income = values["eps"], values["net_income"]
        equity, assets, liabilities = values["equity"], values["assets"], values["liabilities"]
        periods = set(eps) | set(net_income) | set(equity) | set(assets) | set(liabilities)
        if not periods:
            continue
        period = max(periods)
        ttm_eps = _ttm(eps, period)
        if ttm_eps is not None:
            entry["eps"] = ttm_eps
        ttm_income = _ttm(net_income, period)
        book = equity.get(period)
        if ttm_income is not None and book:
            entry["roe"] = ttm_income / book * 100
        total_assets, total_liabilities = assets.get(period), liabilities.get(period)
        if total_assets and total_liabilities is not None:
            entry["debtRatio"] = total_liabilities / total_assets * 100
        if book:
            entry["equity"] = book

    monthly: dict[tuple[int, int], float] = {}
    for row in revenue_rows:
        try:
            year, month = int(row["revenue_year"]), int(row["revenue_month"])
            monthly[(year, month)] = float(row["revenue"])
        except (KeyError, TypeError, ValueError):
            continue
    for (year, month), value in sorted(monthly.items()):
        previous = monthly.get((year - 1, month))
        if previous:
            events[revenue_publication(year, month)]["revenueYoY"] = (value / previous - 1) * 100

    timeline: list[tuple[str, dict[str, float]]] = []
    carried: dict[str, float] = {}
    for available in sorted(events):
        carried = {**carried, **events[available]}
        timeline.append((available, dict(carried)))
    return timeline


class PointInTimeFundamentals:
    """Fundamentals as they were knowable on a given date."""

    def __init__(self, timelines: dict[str, list[tuple[str, dict[str, float]]]]):
        self.timelines = timelines
        self._dates = {code: [item[0] for item in rows] for code, rows in timelines.items()}

    @classmethod
    def from_cache(cls, cache_dir: Path) -> "PointInTimeFundamentals":
        folder = cache_dir / CACHE_SUBDIR / "stocks"
        if not folder.exists():
            raise SystemExit(f"找不到歷史財報快取：{folder}")
        timelines = {}
        for path in sorted(folder.glob("*.json")):
            timeline = build_stock(json.loads(path.read_text(encoding="utf-8")))
            if timeline:
                timelines[path.stem] = timeline
        return cls(timelines)

    def as_of(self, day: str) -> dict[str, dict[str, float]]:
        """Latest published figures for every company as of ``day``."""
        snapshot = {}
        for code, dates in self._dates.items():
            position = bisect_right(dates, day)
            if position:
                snapshot[code] = dict(self.timelines[code][position - 1][1])
        return snapshot

    def coverage(self, day: str) -> int:
        return len(self.as_of(day))


def inspect(cache_dir: Path, limit: int = 40) -> None:
    """Print the statement ``type`` values actually present, to verify FIELDS."""
    folder = cache_dir / CACHE_SUBDIR / "stocks"
    seen: dict[str, set[str]] = defaultdict(set)
    for path in sorted(folder.glob("*.json"))[:20]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for dataset in ("TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet"):
            for row in payload.get(dataset, []):
                seen[dataset].add(str(row.get("type", "")))
    for dataset, types in seen.items():
        print(f"{dataset}: {len(types)} 種 type")
        for name in sorted(types)[:limit]:
            print(f"   {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="組裝 point-in-time 歷史基本面")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--inspect", action="store_true", help="列出快取實際存在的欄位名稱")
    parser.add_argument("--as-of", help="列出該日期可知的基本面覆蓋數")
    args = parser.parse_args()

    if args.inspect:
        inspect(args.cache_dir)
        return
    series = PointInTimeFundamentals.from_cache(args.cache_dir)
    if args.as_of:
        snapshot = series.as_of(args.as_of)
        print(f"{args.as_of} 可知基本面：{len(snapshot)} 檔")
        for code, metrics in list(snapshot.items())[:5]:
            print(f"  {code}: " + ", ".join(f"{k}={v:.2f}" for k, v in metrics.items()))
        return
    print(f"公司數：{len(series.timelines)}")
    for code, rows in list(series.timelines.items())[:5]:
        print(f"  {code}: {len(rows)} 個公告時點，{rows[0][0]} → {rows[-1][0]}")


if __name__ == "__main__":
    main()
