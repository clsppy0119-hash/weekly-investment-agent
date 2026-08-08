"""A shortlist is judged against the pool it was drawn from.

Measuring only against 0050 mixes two questions: did ranking pick well, and did
the eligible universe happen to beat a large-cap index.  When the product is a
shortlist, the first question is the one that matters, so each split also
reports the picks against an equal-weighted holding of everything eligible.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring import candidates
from strategy_backtest import run_range


def _quotes(count, volume=5_000_000.0):
    return {f"{1101 + index}": {"price": 100.0, "volume": volume, "ma5": 90.0,
                                "ma20": 90.0, "change": 1.0} for index in range(count)}


def test_pool_is_the_whole_eligible_list_not_the_shortlist():
    quotes = _quotes(12)
    funds = {code: {} for code in quotes}

    everything = candidates("swing", quotes, funds, None)
    shortlist = candidates("swing", quotes, funds, 3)

    assert len(everything) == 12, "picks=None must return the full ranked pool"
    assert shortlist == everything[:3], "the shortlist is the head of that pool"


def _history_where_the_leader_wins(days=60):
    """Every name rises; the highest-scoring one rises fastest."""
    history, dates = [], []
    for index in range(days):
        dates.append(f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}")
        day = {}
        for rank in range(6):
            # Rank 0 has the strongest daily change, so it scores highest.
            day[f"{1101 + rank}"] = (100.0 + index * (1.5 - rank * 0.2), 5_000_000.0)
        history.append(day)
    return history, dates


def test_a_shortlist_that_outperforms_its_pool_reports_a_positive_edge():
    history, dates = _history_where_the_leader_wins()
    result = run_range(history, dates, 0, len(history), "swing",
                       picks=1, holding=5, min_volume=0, continuous_trend=True)

    pool = result["versusEligiblePool"]
    assert pool["rebalances"] > 0
    assert pool["meanExcessPerRebalance"] > 0, "the fastest riser must beat the pool average"
    assert pool["medianPoolSize"] >= 2, "the pool must be wider than the shortlist"


def test_binary_scoring_ties_hand_the_pick_to_the_tie_break():
    """Why the continuous form exists, pinned on a case small enough to check.

    Six names rise at different speeds. Rounded binary scores tie, so selection
    falls through to the tie-break instead of to merit, and the shortlist stops
    beating its own pool.
    """
    history, dates = _history_where_the_leader_wins()
    binary = run_range(history, dates, 0, len(history), "swing",
                       picks=1, holding=5, min_volume=0)
    continuous = run_range(history, dates, 0, len(history), "swing",
                           picks=1, holding=5, min_volume=0, continuous_trend=True)

    assert binary["versusEligiblePool"]["meanExcessPerRebalance"] < 0
    assert continuous["versusEligiblePool"]["meanExcessPerRebalance"] > 0


def test_holding_the_entire_pool_shows_no_edge_over_it():
    history, dates = _history_where_the_leader_wins()
    result = run_range(history, dates, 0, len(history), "swing",
                       picks=99, holding=5, min_volume=0)

    pool = result["versusEligiblePool"]
    assert abs(pool["meanExcessPerRebalance"]) < 1e-9, "buying the pool cannot beat the pool"
    assert pool["conclusive"] is False


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
