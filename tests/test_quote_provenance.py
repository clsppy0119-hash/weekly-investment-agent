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
from quote_provenance import available_at, build, conflict_status, roc_date, trading_date

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


def test_roc_dates_are_converted():
    assert roc_date("1150807") == "2026-08-07"
    assert roc_date("1140101") == "2025-01-01"


def test_unusable_roc_values_are_refused():
    for value in ("", None, "2026-08-07", "115080", "1159999"):
        assert roc_date(value) is None


def test_the_session_comes_from_the_rows_not_the_run_date():
    """A Sunday push must be stamped with Friday's session, not Sunday."""
    assert trading_date(["1150807", "1150807"], "2026-08-09") == "2026-08-07"


def test_the_latest_session_wins_when_feeds_disagree():
    assert trading_date(["1150806", "1150807"], "2026-08-09") == "2026-08-07"


def test_the_run_date_is_only_a_last_resort():
    assert trading_date([], "2026-08-07") == "2026-08-07"
    assert trading_date(["nonsense"], "2026-08-07") == "2026-08-07"


def test_the_modelled_assumption_is_recorded_in_the_record():
    provenance = build("2026-08-07", RETRIEVED, {}, {}, {"twse": [], "tpex": []})

    assert "modelled" in provenance["quote"]["availableAtBasis"]


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")

