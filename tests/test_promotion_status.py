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


def _state(horizon, excesses, field="poolExcessPct"):
    """A shortlist is judged against its pool; autonomy against 0050."""
    return {"recommendations": [
        {"date": day, "outcomes": {str(horizon): {"status": "complete", field: value}}}
        for day, value in zip(_dates(len(excesses)), excesses)
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


def test_beating_only_the_index_does_not_earn_assisted_selection():
    """Beating 0050 can be the pool's size tilt; the claim is about the pick."""
    steady = [2.0, 2.2, 1.9, 2.1, 2.3, 1.8] * 6
    report = assess(_state(20, steady, field="excessReturnPct"), {})

    assert report["stage"] == "screening_assistant"
    assert "對合格池" in report["blockers"]["assisted_selection"][0]


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
    # Autonomy is judged against 0050; the 20-day pool evidence carries the
    # assisted stage beneath it.
    state = _state(60, steady, field="excessReturnPct")
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
