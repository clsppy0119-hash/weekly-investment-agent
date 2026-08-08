"""Trend scoring: the shipped binary form, and the continuous alternative.

The binary form gives a stock 1% above its average the same score as one 30%
above, so scores tie in large blocks and the ranking ends up decided by the
tie-break rather than by merit.  The continuous form keeps the distance.  It is
opt-in, so production behaviour must stay untouched until a backtest earns it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring import metrics, trend_score


def test_binary_form_is_unchanged():
    assert trend_score(110, 100, continuous=False) == 75
    assert trend_score(100, 100, continuous=False) == 75
    assert trend_score(90, 100, continuous=False) == 35


def test_binary_form_cannot_tell_a_small_lead_from_a_large_one():
    assert trend_score(101, 100, False) == trend_score(130, 100, False)


def test_continuous_form_separates_them():
    near = trend_score(101, 100, True)
    far = trend_score(130, 100, True)

    assert far > near, "distance above the average must matter"
    assert near > 50 > trend_score(95, 100, True)


def test_continuous_form_stays_inside_the_score_range():
    assert trend_score(1000, 100, True) == 100.0
    assert trend_score(1, 100, True) == 0.0


def test_missing_or_invalid_inputs_score_nothing():
    for continuous in (False, True):
        assert trend_score(None, 100, continuous) is None
        assert trend_score(100, None, continuous) is None
        assert trend_score(100, 0, continuous) is None


def test_metrics_defaults_to_the_shipped_binary_form():
    quote = {"price": 130.0, "ma20": 100.0, "ma5": 100.0}

    assert metrics(quote, {})["trend20"] == 75
    assert metrics(quote, {}, continuous_trend=True)["trend20"] > 75


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
