"""A fully labelled snapshot must certify, and each missing label must block.

Four contract records had been failing at once, so the report discarded every
ranked candidate and printed the same message whether the data was absent,
stale, or merely unlabelled. This pins the whole path: labelled evidence
certifies, and removing any one label puts the specific blocker back.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_contract import build_contract
from point_in_time_universe import named_certification
from quote_provenance import available_at, build

TRADING_DAY = "2026-08-07"
RETRIEVED = "2026-08-07T09:30:00+00:00"
QUOTES = {"6996": {"price": 92.0}, "6782": {"price": 210.0}}
FUNDAMENTALS = {"6996": {"eps": 4.1}, "6782": {"eps": 9.7}}


def _snapshot():
    return {
        "updatedAt": RETRIEVED,
        "provenance": build(TRADING_DAY, RETRIEVED, QUOTES, FUNDAMENTALS,
                            {"twse": ["6996"], "tpex": ["6782"]}),
        "quotes": QUOTES,
        "fundamentals": FUNDAMENTALS,
    }


def _actions():
    return {
        "source": "FinMind authorized API",
        "dataset": "TaiwanStockDividendResult",
        "period": {"start": "2026-05-07", "end": TRADING_DAY},
        "effectiveDate": TRADING_DAY,
        "availableAt": available_at(TRADING_DAY),
        "conflictStatus": "no_conflict",
        "events": [],
    }


def _pit():
    entries = {"6996": "2021-11-08", "6782": "2018-03-14"}
    return {
        "generatedAt": f"{TRADING_DAY}T15:56:31+00:00",
        # The universe is incomplete, as it genuinely is in production.
        "certified": False,
        "candidateCertification": named_certification(["6996", "6782"], entries, {}, set()),
    }


def test_a_fully_labelled_snapshot_certifies():
    contract = build_contract(_snapshot(), _actions(), {}, _pit())

    assert contract["certified"] is True, contract["blockers"]
    assert contract["blockers"] == []


def test_an_unlabelled_quote_source_blocks_again():
    snapshot = _snapshot()
    del snapshot["provenance"]

    contract = build_contract(snapshot, _actions(), {}, _pit())

    assert "contract_quote_source_missing" in contract["blockers"]


def test_corporate_actions_without_availability_block_again():
    actions = _actions()
    actions["availableAt"] = None

    contract = build_contract(_snapshot(), actions, {}, _pit())

    assert "contract_corporate_actions_as_of_missing" in contract["blockers"]


def test_a_candidate_without_listing_evidence_blocks_again():
    pit = _pit()
    pit["candidateCertification"] = named_certification(["6996", "9999"], {"6996": "2021-11-08"}, {}, set())

    contract = build_contract(_snapshot(), _actions(), {}, pit)

    assert "contract_point_in_time_pit_candidate_not_certified" in contract["blockers"]


def test_news_never_counts_toward_certification():
    contract = build_contract(_snapshot(), _actions(), {}, _pit())
    news = next(item for item in contract["records"] if item["name"] == "market_news")

    assert news["role"] == "context_only"
    assert contract["certified"] is True, "headlines must not be decision evidence"


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
