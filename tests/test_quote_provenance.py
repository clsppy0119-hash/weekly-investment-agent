"""Quote provenance must be labelled honestly, not merely present.

The contract gate discarded every ranked candidate because quotes.json carried
no source. Filling that in is only correct if the values are defensible: fetch
time must not masquerade as publication time, and disjoint exchanges must be
verified rather than assumed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_contract import build_contract
from quote_provenance import available_at, build, conflict_status, trading_date

RETRIEVED = "2026-08-07T09:30:00+00:00"


def test_availability_follows_the_session_not_the_fetch():
    published = available_at("2026-08-07")

    assert published.startswith("2026-08-07T14:00")
    assert published < RETRIEVED.replace("09:30:00+00:00", "23:59:59+08:00")
    assert "09:30" not in published, "retrieval time must never stand in for publication"


def test_disjoint_exchanges_are_no_conflict():
    status, overlaps = conflict_status({"twse": ["2330", "2454"], "tpex": ["6488", "5274"]})

    assert status == "no_conflict"
    assert overlaps == []


def test_an_overlapping_code_is_reported_not_assumed_away():
    status, overlaps = conflict_status({"twse": ["2330", "6488"], "tpex": ["6488"]})

    assert status == "conflict_unresolved"
    assert overlaps == ["6488"]


def test_a_labelled_snapshot_certifies_the_quote_and_fundamentals_records():
    quotes = {"2330": {"price": 1000.0}, "6488": {"price": 500.0}}
    fundamentals = {"2330": {"eps": 40.0}, "6488": {"eps": 12.0}}
    provenance = build("2026-08-07", RETRIEVED, quotes, fundamentals,
                       {"twse": ["2330"], "tpex": ["6488"]})

    assert provenance["quote"]["quality"] == "verified"
    assert provenance["fundamentals"]["quality"] == "verified"

    contract = build_contract({"provenance": provenance, "quotes": quotes,
                               "fundamentals": fundamentals, "updatedAt": RETRIEVED},
                              {}, {}, {})
    blocked = {item for item in contract["blockers"]}
    assert not any(item.startswith("contract_quote_") for item in blocked)
    assert not any(item.startswith("contract_fundamentals_") for item in blocked)


def test_an_overlap_keeps_the_snapshot_out_of_verified():
    quotes = {"6488": {"price": 500.0}}
    provenance = build("2026-08-07", RETRIEVED, quotes, {},
                       {"twse": ["6488"], "tpex": ["6488"]})

    assert provenance["quote"]["conflictStatus"] == "conflict_unresolved"
    assert provenance["quote"]["quality"] != "verified"


def test_a_holiday_run_keeps_the_previous_session_date():
    """The weekday schedule fires on holidays; the exchanges do not open."""
    unchanged = {"2330": {"price": 1000.0}, "6488": {"price": 500.0}}

    assert trading_date("2026-10-10", unchanged, unchanged, "2026-10-09") == "2026-10-09"


def test_a_moved_price_means_a_new_session():
    previous = {"2330": {"price": 1000.0}}
    today = {"2330": {"price": 1015.0}}

    assert trading_date("2026-10-12", today, previous, "2026-10-09") == "2026-10-12"


def test_a_first_ever_run_uses_the_run_date():
    assert trading_date("2026-08-07", {"2330": {"price": 1000.0}}, {}, None) == "2026-08-07"


def test_the_modelled_assumption_is_recorded_in_the_record():
    provenance = build("2026-08-07", RETRIEVED, {}, {}, {"twse": [], "tpex": []})

    assert "modelled" in provenance["quote"]["availableAtBasis"]


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
