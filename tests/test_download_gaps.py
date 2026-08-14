"""A partial download must not look like a complete one.

Both fetchers give up quietly: a day returns None, a month returns []. The
backtest indexes history by position, so a hole does not raise -- it silently
stretches "twenty trading days later" into whatever the gap spans. A real run
ended with three years missing from the middle and still printed a success
line, and the factor results computed on it were wrong in a way nothing showed.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import MAX_HOLIDAY_RUN, report_gaps


def _weekdays(start: str, end: str):
    cursor, last = date.fromisoformat(start), date.fromisoformat(end)
    while cursor <= last:
        if cursor.weekday() < 5:
            yield cursor.isoformat()
        cursor += timedelta(days=1)


def _cache(start, end, drop=()):
    dropped = set(drop)
    return {day: {} for day in _weekdays(start, end) if day not in dropped}


def test_a_continuous_cache_says_nothing(capsys):
    report_gaps(_cache("2026-01-05", "2026-01-30"), [])

    assert capsys.readouterr().out == ""


def test_a_long_hole_is_reported(capsys):
    missing = list(_weekdays("2026-01-12", "2026-02-13"))
    report_gaps(_cache("2026-01-05", "2026-03-06", drop=missing), [])
    output = capsys.readouterr().out

    assert "資料不連續" in output
    assert "2026-01-12" in output
    assert "位置索引" in output, "the reader has to know why a hole matters"


def test_a_new_year_closure_is_not_reported(capsys):
    """Warning on every February would train the reader to ignore it."""
    closure = list(_weekdays("2026-02-16", "2026-02-24"))
    assert len(closure) <= MAX_HOLIDAY_RUN

    report_gaps(_cache("2026-01-05", "2026-03-20", drop=closure), [])

    assert "資料不連續" not in capsys.readouterr().out


def test_failed_days_are_reported_even_without_a_hole(capsys):
    report_gaps(_cache("2026-01-05", "2026-01-30"), ["2026-01-08"])
    output = capsys.readouterr().out

    assert "抓取失敗" in output
    assert "2026-01-08" in output


def test_an_almost_empty_cache_does_not_crash(capsys):
    report_gaps({}, [])
    report_gaps({"2026-01-05": {}}, [])

    assert capsys.readouterr().out == ""


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
