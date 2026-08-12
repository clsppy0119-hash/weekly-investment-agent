"""Fundamentals must not be visible before they were filed.

A Q2 statement describes 30 June but reaches the market in mid-August.  Scoring
a July signal with it would hand the strategy earnings nobody could have known,
which is the same look-ahead the price engine was fixed for.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from point_in_time_fundamentals import (
    PointInTimeFundamentals, build_stock, period_end, quarter_publication, revenue_publication,
)


def _statement(period, type_name, value, available_at=None):
    result = {"date": period, "type": type_name, "value": value}
    if available_at is not None:
        result["availableAt"] = available_at
    return result


def _payload():
    """Four quarters of statements plus two years of June revenue."""
    quarters = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    statements, balance = [], []
    for index, period in enumerate(quarters):
        statements.append(_statement(period, "EPS", 1.0 + index))
        statements.append(_statement(period, "IncomeAfterTaxes", 1_000_000 * (index + 1)))
        balance.append(_statement(period, "TotalEquity", 50_000_000))
        balance.append(_statement(period, "TotalAssets", 100_000_000))
        balance.append(_statement(period, "TotalLiabilities", 40_000_000))
    revenue = [
        {"revenue_year": 2024, "revenue_month": 6, "revenue": 100.0},
        {"revenue_year": 2025, "revenue_month": 6, "revenue": 150.0},
    ]
    return {
        "TaiwanStockFinancialStatements": statements,
        "TaiwanStockBalanceSheet": balance,
        "TaiwanStockMonthRevenue": revenue,
    }


def _five_year_payload(*, omit=None, late=None):
    statements, balance = [], []
    for year in range(2020, 2025):
        for suffix in ("03-31", "06-30", "09-30", "12-31"):
            period = f"{year}-{suffix}"
            available = f"{quarter_publication(period)}T18:00:00+08:00"
            for kind, rows, value in (
                ("EPS", statements, 1.0),
                ("IncomeAfterTaxes", statements, 1_000_000.0),
                ("TotalEquity", balance, 50_000_000.0),
                ("TotalAssets", balance, 100_000_000.0),
                ("TotalLiabilities", balance, 40_000_000.0),
            ):
                if omit == (period, kind):
                    continue
                row_available = late if late and late[0] == (period, kind) else available
                if late and late[0] == (period, kind):
                    row_available = late[1]
                rows.append(_statement(period, kind, value, row_available))
    return {
        "TaiwanStockFinancialStatements": statements,
        "TaiwanStockBalanceSheet": balance,
        "TaiwanStockMonthRevenue": [],
    }


def test_filing_deadlines_follow_the_statutory_calendar():
    assert quarter_publication("2025-03-31") == "2025-05-15"
    assert quarter_publication("2025-06-30") == "2025-08-14"
    assert quarter_publication("2025-09-30") == "2025-11-14"
    # The annual report lands the following March, not in December.
    assert quarter_publication("2025-12-31") == "2026-03-31"
    assert revenue_publication(2025, 6) == "2025-07-10"
    assert revenue_publication(2025, 12) == "2026-01-10"


def test_the_three_period_forms_all_resolve_to_the_same_close():
    """The pipeline carries ISO, AD-quarter and ROC-quarter for one period."""
    assert period_end("2026-06-30") == "2026-06-30"
    assert period_end("2026Q2") == "2026-06-30"
    assert period_end("115Q2") == "2026-06-30"
    assert period_end("2026-06-30T00:00:00") == "2026-06-30"


def test_unusable_period_values_resolve_to_nothing():
    for value in ("", None, "Q2", "2026", "2026Q9", "nonsense"):
        assert period_end(value) is None


def test_a_reported_period_maps_to_its_filing_deadline():
    assert quarter_publication(period_end("115Q2")) == "2026-08-14"
    assert quarter_publication(period_end("2025Q4")) == "2026-03-31"


def test_a_quarter_is_invisible_until_it_is_filed():
    series = PointInTimeFundamentals({"1111": build_stock(_payload())})

    assert "1111" not in series.as_of("2025-05-14"), "Q1 must not leak before 15 May"
    assert "1111" in series.as_of("2025-05-15")

    # Q4 closes on 31 December but only becomes knowable the next March.
    assert "debtRatio" in series.as_of("2026-01-01")["1111"]
    before = series.as_of("2026-03-30")["1111"]
    after = series.as_of("2026-03-31")["1111"]
    assert before != after, "the annual filing must change what is knowable"


def test_monthly_revenue_waits_for_the_tenth():
    series = PointInTimeFundamentals({"1111": build_stock(_payload())})

    assert "revenueYoY" not in series.as_of("2025-07-09").get("1111", {})
    assert series.as_of("2025-07-10")["1111"]["revenueYoY"] == 50.0


def test_derived_ratios_use_only_the_reported_period():
    series = PointInTimeFundamentals({"1111": build_stock(_payload())})
    snapshot = series.as_of("2026-03-31")["1111"]

    # Four quarters of EPS 1+2+3+4 trailing.
    assert snapshot["eps"] == 10.0
    # Liabilities 40M of 100M assets.
    assert snapshot["debtRatio"] == 40.0
    # TTM income 10M on 50M equity.
    assert snapshot["roe"] == 20.0


def test_values_carry_forward_between_filings():
    series = PointInTimeFundamentals({"1111": build_stock(_payload())})
    at_filing = series.as_of("2025-08-14")["1111"]
    a_month_later = series.as_of("2025-09-15")["1111"]

    assert at_filing == a_month_later, "no new filing means no new information"


def test_financial_history_requires_five_complete_years_and_all_five_fields():
    series = PointInTimeFundamentals({"1111": build_stock(_five_year_payload())})
    assert series.as_of("2025-04-01")["1111"]["financialHistoryYears"] == 5

    incomplete = PointInTimeFundamentals({
        "1111": build_stock(_five_year_payload(omit=("2020-03-31", "EPS")))
    })
    assert incomplete.as_of("2025-04-01")["1111"]["financialHistoryYears"] == 4


def test_one_quarter_or_one_field_per_year_never_counts_as_full_history():
    payload = _five_year_payload()
    payload["TaiwanStockFinancialStatements"] = [
        row for row in payload["TaiwanStockFinancialStatements"]
        if row["type"] == "EPS" and row["date"].endswith("12-31")
    ]
    payload["TaiwanStockBalanceSheet"] = []
    series = PointInTimeFundamentals({"1111": build_stock(payload)})
    assert series.as_of("2025-04-01")["1111"]["financialHistoryYears"] == 0


def test_late_backfill_cannot_rewrite_an_earlier_history_snapshot():
    missing = ("2020-03-31", "EPS")
    payload = _five_year_payload(omit=missing)
    payload["TaiwanStockFinancialStatements"].append(
        _statement(missing[0], missing[1], 1.0, "2027-01-15T10:00:00+08:00")
    )
    series = PointInTimeFundamentals({"1111": build_stock(payload)})
    assert series.as_of("2026-12-31")["1111"]["financialHistoryYears"] == 4
    assert series.as_of("2027-01-15")["1111"]["financialHistoryYears"] == 5


def test_late_value_correction_never_borrows_the_original_availability():
    payload = _payload()
    payload["TaiwanStockFinancialStatements"].append(
        _statement("2025-03-31", "EPS", 1000.0, "2027-01-15T10:00:00+08:00")
    )
    series = PointInTimeFundamentals({"1111": build_stock(payload)})
    assert series.as_of("2026-03-31")["1111"]["eps"] == 10.0
    assert series.as_of("2027-01-14")["1111"]["eps"] == 10.0
    assert series.as_of("2027-01-15")["1111"]["eps"] == 1009.0


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
