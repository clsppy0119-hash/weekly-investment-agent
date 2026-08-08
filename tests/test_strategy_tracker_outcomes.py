"""Outcome tracking must survive the rolling quote window and stay frozen.

``quotes.json`` only carries a few days of history, so outcomes recomputed from
it directly could never reach the 20- and 60-day horizons, and the ones that did
settle drifted as the window slid.  Each recommendation now keeps its own
append-only price trail; these tests pin that behaviour, the cost model, and the
0050 comparison.
"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_tracker import load_state, record_recommendations, review_summary


def _quote_data(rows_1111, rows_0050):
    return {
        "updatedAt": "2026-01-01 00:00 Taipei time",
        "quotes": {
            "1111": {"name": "測試", "price": 100.0, "ma5": None, "ma20": None},
            "0050": {"name": "台灣50", "price": 100.0},
        },
        "fundamentals": {"1111": {"revenueYoY": 10.0, "eps": 5.0, "roe": 15.0, "debtRatio": 30.0,
                                  "financialHistoryYears": 6}},
        "history": {"1111": rows_1111, "0050": rows_0050},
    }


def _rows(pairs):
    return [{"date": day, "close": close, "volume": 1_000_000.0} for day, close in pairs]


def _ranked():
    quote = {"name": "測試", "price": 100.0, "ma5": None, "ma20": None}
    fund = {"revenueYoY": 10.0, "eps": 5.0, "roe": 15.0, "debtRatio": 30.0, "financialHistoryYears": 6}
    return {"comprehensive": [(90, 85, "1111", quote, fund)]}


def _outcome_after_window_slide(path):
    """Two runs whose history windows do not overlap, as the real feed behaves."""
    first = _quote_data(
        _rows([("2026-01-02", 101.0), ("2026-01-03", 102.0), ("2026-01-05", 103.0)]),
        _rows([("2026-01-02", 100.5), ("2026-01-03", 101.0), ("2026-01-05", 101.5)]),
    )
    record_recommendations("2026-01-01", "comprehensive", _ranked(), first, path)

    second = _quote_data(
        _rows([("2026-01-06", 104.0), ("2026-01-07", 110.0), ("2026-01-08", 115.0)]),
        _rows([("2026-01-06", 102.0), ("2026-01-07", 105.0), ("2026-01-08", 106.0)]),
    )
    state = record_recommendations("2026-01-08", "comprehensive", _ranked(), second, path)
    return state["recommendations"][0]


def test_horizon_settles_across_a_slid_window():
    with TemporaryDirectory() as folder:
        path = Path(folder) / "recommendations.json"
        item = _outcome_after_window_slide(path)

        assert len(item["priceTrail"]) == 6, "the trail must keep days the feed has dropped"
        five = item["outcomes"]["5"]
        assert five["status"] == "complete"
        # Fifth observed day after entry is 01-07 at 110, not the fifth row of
        # whatever window happened to be loaded.
        assert five["date"] == "2026-01-07"
        assert five["grossReturnPct"] == 10.0


def test_longer_horizons_stay_pending_rather_than_settling_wrong():
    with TemporaryDirectory() as folder:
        path = Path(folder) / "recommendations.json"
        item = _outcome_after_window_slide(path)

        assert item["outcomes"]["20"]["status"] == "pending"
        assert item["outcomes"]["20"]["observations"] == 6
        assert item["outcomes"]["60"]["status"] == "pending"


def test_costs_and_benchmark_are_applied():
    with TemporaryDirectory() as folder:
        path = Path(folder) / "recommendations.json"
        five = _outcome_after_window_slide(path)["outcomes"]["5"]

        assert five["netReturnPct"] < five["grossReturnPct"], "round-trip costs must be charged"
        assert 9.0 < five["netReturnPct"] < 9.3, five["netReturnPct"]
        # 0050 went 100 -> 105 over the same window and carries the lighter ETF
        # sell tax: 9.14% - 4.39% of excess is what the strategy actually added.
        assert 4.7 < five["excessReturnPct"] < 4.8, five["excessReturnPct"]
        assert five["priceReturnOnly"] is True, "the dividend limitation must stay visible"


def test_a_settled_outcome_is_never_revised():
    with TemporaryDirectory() as folder:
        path = Path(folder) / "recommendations.json"
        _outcome_after_window_slide(path)
        settled = dict(load_state(path)["recommendations"][0]["outcomes"]["5"])

        # A later run sees a different price for the same day; history is not
        # allowed to rewrite a result that has already been booked.
        tampered = _quote_data(
            _rows([("2026-01-07", 999.0), ("2026-01-09", 120.0)]),
            _rows([("2026-01-09", 107.0)]),
        )
        record_recommendations("2026-01-09", "comprehensive", _ranked(), tampered, path)

        assert load_state(path)["recommendations"][0]["outcomes"]["5"] == settled


def test_summary_reports_each_horizon_separately():
    with TemporaryDirectory() as folder:
        path = Path(folder) / "recommendations.json"
        _outcome_after_window_slide(path)
        summary = review_summary(load_state(path))

        assert "5 日" in summary and "20 日" in summary and "60 日" in summary
        assert "尚未結算" in summary, "unsettled horizons must not be hidden"


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
