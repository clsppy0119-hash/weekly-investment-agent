"""Promotion between roles is decided by a rule fixed in advance.

Moving from a shortlist someone checks to one they follow is a claim about
evidence. Writing the thresholds down means a good month cannot argue its way
past them, and that the report can say exactly what is still missing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promotion_status import assess

OPEN_GATE = {"adviceEnabled": True}


def _dates(count):
    """Distinct decision dates: same-day picks are one decision, not several."""
    return [f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(count)]


def _state(horizon, excesses, versus_pool=None):
    """Every stage needs both: beating 0050, and beating the pool.

    0050 is what the user would otherwise hold, so losing to it makes the
    shortlist worse than doing nothing. Beating the pool as well is what
    separates a repeatable edge from a universe that happened to run hot.
    """
    pool = excesses if versus_pool is None else versus_pool
    return {"recommendations": [
        {"date": day, "outcomes": {str(horizon): {
            "status": "complete", "excessReturnPct": index, "poolExcessPct": against_pool}}}
        for day, index, against_pool in zip(_dates(len(excesses)), excesses, pool)
    ]}


def test_an_empty_tracker_is_research_only():
    assert assess({"recommendations": []}, {})["stage"] == "research_only"


def test_recording_candidates_earns_the_screening_role():
    report = assess(_state(20, [1.0, -0.5]), {})

    assert report["stage"] == "screening_assistant"
    assert "assisted_selection" in report["blockers"]


def test_a_thin_sample_cannot_reach_assisted_selection():
    # Strongly positive, but only ten settled outcomes.
    report = assess(_state(20, [3.0] * 10), {})

    assert report["stage"] == "screening_assistant"
    assert "已結算 10/30 個決策日" in report["blockers"]["assisted_selection"][0]


def test_same_day_picks_count_as_one_decision():
    """Three picks share the day's market move; they are not three draws."""
    state = {"recommendations": [
        {"date": "2026-01-05", "outcomes": {"20": {"status": "complete", "poolExcessPct": value}}}
        for value in (2.0, 2.1, 1.9)
    ]}
    report = assess(state, {})

    assert report["versusEligiblePool"]["20"]["settled"] == 1
    assert report["versusEligiblePool"]["20"]["outcomes"] == 3


def test_beating_only_the_index_is_not_enough():
    """The pool may simply have run hot; that does not repeat."""
    steady = [2.0, 2.2, 1.9, 2.1, 2.3, 1.8] * 6
    flat = [0.1, -0.1] * 18
    report = assess(_state(20, steady, versus_pool=flat), {})

    assert report["stage"] == "screening_assistant"
    assert any("對合格池" in item for item in report["blockers"]["assisted_selection"])


def test_beating_only_the_pool_is_not_enough_either():
    """Ranking well inside a universe that loses to 0050 helps nobody.

    The alternative to using the system is buying 0050, so a shortlist that
    loses to it is worse than doing nothing however well it ranks.
    """
    steady = [2.0, 2.2, 1.9, 2.1, 2.3, 1.8] * 6
    losing = [-2.0, -2.2, -1.9, -2.1, -2.3, -1.8] * 6
    report = assess(_state(20, losing, versus_pool=steady), {})

    assert report["stage"] == "screening_assistant"
    assert any("對 0050" in item for item in report["blockers"]["assisted_selection"])


def test_a_large_but_noisy_sample_is_still_blocked():
    noisy = [12.0, -11.0] * 20
    report = assess(_state(20, noisy), {})

    assert report["stage"] == "screening_assistant"
    assert "橫跨 0" in report["blockers"]["assisted_selection"][0]


def test_a_consistent_edge_over_enough_outcomes_promotes():
    steady = [1.8, 2.2, 2.0, 1.9, 2.1, 2.3] * 6
    report = assess(_state(20, steady), {})

    assert report["stage"] == "assisted_selection"


def test_autonomy_also_requires_the_advice_gate():
    steady = [1.8, 2.2, 2.0, 1.9, 2.1, 2.3] * 12
    # Sixty decision dates at the long horizon, and the twenty-day evidence
    # that carries the assisted stage beneath it.
    state = _state(60, steady)
    state["recommendations"] += _state(20, steady)["recommendations"]

    closed = assess(state, {"adviceEnabled": False})
    assert closed["stage"] != "autonomous_selection"
    assert "investment-advice-gate 尚未開啟" in closed["blockers"]["autonomous_selection"]

    assert assess(state, OPEN_GATE)["stage"] == "autonomous_selection"


def test_a_reliable_loss_never_promotes():
    losing = [-1.8, -2.2, -2.0, -1.9, -2.1, -2.3] * 6
    report = assess(_state(20, losing), {})

    assert report["stage"] == "screening_assistant"
    assert "顯著為負" in report["blockers"]["assisted_selection"][0]


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
