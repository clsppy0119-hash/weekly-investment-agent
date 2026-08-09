"""The two feeds compute ROE from different definitions, so one has to win.

The open-data feed annualises year-to-date profit -- latest period times
4/quarters -- over closing equity. That assumes every remaining quarter repeats
the last, which overstates any business with a season, and divides a year of
profit by a single day's equity. FinMind sums four actual quarters over average
equity, the standard trailing definition.

They therefore disagree by design. Treating that as a source conflict would
block every candidate for ever, so ROE is pinned to the standard definition and
excluded from the conflict check, while figures taken straight off the
statements stay in it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finmind_fundamentals import CONFLICT_FIELDS, CONFLICT_TOLERANCE, ROE_SOURCE, _numeric


def annualised_over_closing(year_to_date, quarters, closing_equity):
    """What the open-data feed computes."""
    return year_to_date / closing_equity * 100 * 4 / quarters


def trailing_over_average(last_four_quarters, opening_equity, closing_equity):
    """What FinMind computes, and what ROE conventionally means."""
    return last_four_quarters / ((opening_equity + closing_equity) / 2) * 100


def test_a_strong_half_year_makes_the_annualised_form_overstate():
    """Half a good year is not a good year."""
    # 10 earned in H1; the preceding half year earned only 2.
    annualised = annualised_over_closing(year_to_date=10, quarters=2, closing_equity=100)
    trailing = trailing_over_average(last_four_quarters=12, opening_equity=90, closing_equity=100)

    assert round(annualised, 1) == 20.0
    assert round(trailing, 1) == 12.6
    assert annualised > trailing * 1.5, "the gap is large enough to change a ranking"


def test_the_two_agree_when_earnings_are_level():
    """With no seasonality and stable equity the definitions converge."""
    annualised = annualised_over_closing(year_to_date=6, quarters=2, closing_equity=100)
    trailing = trailing_over_average(last_four_quarters=12, opening_equity=100, closing_equity=100)

    assert abs(annualised - trailing) < 0.01


def test_roe_is_not_treated_as_a_source_conflict():
    assert "roe" not in CONFLICT_FIELDS, "it disagrees by design; it would block for ever"


def test_statement_figures_are_still_checked():
    """These are read straight off the filings, so they should match."""
    for field in ("eps", "debtRatio", "revenueYoY"):
        assert field in CONFLICT_FIELDS


def test_the_tolerance_admits_rounding_but_not_a_real_gap():
    reported, other = 4.10, 4.15
    assert abs(other - reported) / reported < CONFLICT_TOLERANCE

    reported, other = 4.10, 5.00
    assert abs(other - reported) / reported > CONFLICT_TOLERANCE


def test_the_chosen_definition_is_named_not_implied():
    assert "average_equity" in ROE_SOURCE
    assert _numeric(1.0) and not _numeric(True) and not _numeric(None)


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
