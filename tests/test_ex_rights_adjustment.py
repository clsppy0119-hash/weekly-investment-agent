"""An ex-dividend drop is not a loss the holder took.

A stock going ex-dividend falls by the distribution. The raw close records that
as a loss, and August to September is peak ex-dividend season in Taiwan, so the
record now being accumulated would be biased against every payer in it.

0050 is a total-return index, so the holding must include its distributions to
be comparable. The eligible pool is a price series with no dividend data of its
own, so that comparison stays price-only on both sides -- crediting one side and
not the other would manufacture an edge.
"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_tracker import _dividend_factor, _extend_dividend_factors, record_recommendations

EVENT = {"date": "2026-01-06", "code": "1111", "before_close": 110.0,
         "reference_price": 100.0, "kind": 10.0}


def test_the_exchange_reference_price_gives_the_factor():
    factors = _extend_dividend_factors({}, [EVENT], "1111", "2026-01-01")

    assert factors == {"2026-01-06": 1.1}


def test_events_before_entry_are_not_applied():
    factors = _extend_dividend_factors({}, [EVENT], "1111", "2026-01-10")

    assert factors == {}


def test_another_company_event_is_not_applied():
    assert _extend_dividend_factors({}, [EVENT], "2222", "2026-01-01") == {}


def test_only_events_up_to_the_settlement_day_count():
    factors = {"2026-01-06": 1.1, "2026-01-20": 1.05}

    assert _dividend_factor(factors, "2026-01-07") == 1.1
    assert round(_dividend_factor(factors, "2026-01-31"), 4) == 1.155
    assert _dividend_factor(factors, "2026-01-02") == 1.0


def _quote_data(rows_1111, rows_0050):
    return {
        "updatedAt": "2026-01-01 00:00 Taipei time",
        "quotes": {"1111": {"name": "測試", "price": 100.0}, "0050": {"name": "台灣50", "price": 100.0}},
        "fundamentals": {"1111": {"revenueYoY": 10.0, "eps": 5.0, "roe": 15.0,
                                  "debtRatio": 30.0, "financialHistoryYears": 6}},
        "history": {"1111": rows_1111, "0050": rows_0050},
    }


def _rows(pairs):
    return [{"date": day, "close": close, "volume": 1_000_000.0} for day, close in pairs]


def _ranked():
    quote = {"name": "測試", "price": 100.0}
    fund = {"revenueYoY": 10.0, "eps": 5.0, "roe": 15.0, "debtRatio": 30.0, "financialHistoryYears": 6}
    return {"comprehensive": [(90, 85, "1111", quote, fund)]}


def _settled_five_day(actions):
    """Entry at 100; the price ends flat at 100 after going ex-dividend."""
    with TemporaryDirectory() as folder:
        path = Path(folder) / "recommendations.json"
        data = _quote_data(
            _rows([("2026-01-02", 110.0), ("2026-01-03", 110.0), ("2026-01-05", 110.0),
                   ("2026-01-06", 100.0), ("2026-01-07", 100.0)]),
            _rows([("2026-01-02", 100.0), ("2026-01-03", 100.0), ("2026-01-05", 100.0),
                   ("2026-01-06", 100.0), ("2026-01-07", 100.0)]),
        )
        state = record_recommendations("2026-01-01", "comprehensive", _ranked(), data, path,
                                       actions=actions)
        return state["recommendations"][0]["outcomes"]["5"]


def test_the_raw_return_still_shows_the_drop():
    """Price-only reporting is unchanged; it is what the pool is compared with."""
    outcome = _settled_five_day({"events": [EVENT]})

    assert outcome["grossReturnPct"] == 0.0, "100 to 100 is flat before adjustment"


def test_the_total_return_restores_the_distribution():
    outcome = _settled_five_day({"events": [EVENT]})

    assert outcome["exRightsFactor"] == 1.1
    assert outcome["priceReturnOnly"] is False
    assert outcome["totalReturnNetPct"] > outcome["netReturnPct"]
    # 10% recovered, less the round trip.
    assert 9.0 < outcome["totalReturnNetPct"] < 9.3


def test_the_index_comparison_uses_the_total_return():
    outcome = _settled_five_day({"events": [EVENT]})

    assert outcome["excessReturnPct"] > 0, "flat against a flat index, plus the dividend"


def test_without_events_nothing_is_invented():
    outcome = _settled_five_day({"events": []})

    assert outcome["exRightsFactor"] == 1.0
    assert outcome["priceReturnOnly"] is True
    assert outcome["totalReturnNetPct"] == outcome["netReturnPct"]


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
