"""Point-in-time certification is scoped to what the report claims.

The universe-wide rule is what a backtest needs: one stock without a listing
date means the historical sample may be missing companies, so the sample is
suspect. A shortlist makes a narrower claim -- these named stocks, today -- and
an unrelated microcap with a missing listing date says nothing about it.
Scoping keeps the guarantee exact rather than unreachable.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_contract import _point_in_time_quality
from point_in_time_universe import named_certification

ENTRIES = {"2330": "1994-09-05", "6996": "2021-11-08", "2454": "2001-07-23"}
EXITS = {"1234": "2020-03-31"}
KNOWN_EXITED = {"1234", "5678"}


def test_named_stocks_with_full_evidence_certify():
    result = named_certification(["2330", "6996"], ENTRIES, EXITS, KNOWN_EXITED)

    assert result["certified"] is True
    assert result["missingEntry"] == []


def test_a_named_stock_without_a_listing_date_fails():
    result = named_certification(["2330", "9999"], ENTRIES, EXITS, KNOWN_EXITED)

    assert result["certified"] is False
    assert result["missingEntry"] == ["9999"]


def test_a_delisted_stock_can_never_be_offered_as_current():
    result = named_certification(["2330", "1234"], ENTRIES, EXITS, KNOWN_EXITED)

    assert result["certified"] is False
    assert result["alreadyExited"] == ["1234"]


def test_a_known_exit_without_an_exit_date_fails():
    result = named_certification(["5678"], ENTRIES, EXITS, KNOWN_EXITED)

    assert result["certified"] is False
    assert result["missingExit"] == ["5678"]


def test_an_empty_shortlist_certifies_nothing():
    assert named_certification([], ENTRIES, EXITS, KNOWN_EXITED)["certified"] is False


def test_the_contract_prefers_the_scoped_verdict_when_candidates_are_named():
    # The universe is incomplete, but the two named stocks are fully evidenced.
    status = {"certified": False,
              "candidateCertification": named_certification(["2330", "6996"], ENTRIES, EXITS, KNOWN_EXITED)}

    assert _point_in_time_quality(status) == "verified"


def test_the_scoped_verdict_can_also_block():
    status = {"certified": True,
              "candidateCertification": named_certification(["9999"], ENTRIES, EXITS, KNOWN_EXITED)}

    assert _point_in_time_quality(status) == "pit_candidate_not_certified"


def test_without_named_candidates_the_universe_rule_still_governs():
    assert _point_in_time_quality({"certified": False}) == "pit_not_certified"
    assert _point_in_time_quality({"certified": True}) == "verified"
    assert _point_in_time_quality({"certified": True, "candidateCertification": {"codes": []}}) == "verified"


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
