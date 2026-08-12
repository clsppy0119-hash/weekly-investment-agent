"""Tests for fixed promotion thresholds and user-readable Traditional Chinese output."""
import unittest

from promotion_status import LABELS, assess, render


OPEN_GATE = {"adviceEnabled": True}


def _dates(count):
    return [f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(count)]


def _state(horizon, excesses, versus_pool=None):
    pool = excesses if versus_pool is None else versus_pool
    return {"recommendations": [
        {"date": day, "outcomes": {str(horizon): {
            "status": "complete", "excessReturnPct": index, "poolExcessPct": against_pool}}}
        for day, index, against_pool in zip(_dates(len(excesses)), excesses, pool)
    ]}


class PromotionStatusTests(unittest.TestCase):
    def test_an_empty_tracker_is_research_only(self):
        self.assertEqual(assess({"recommendations": []}, {})["stage"], "research_only")

    def test_recording_candidates_earns_the_screening_role(self):
        report = assess(_state(20, [1.0, -0.5]), {})
        self.assertEqual(report["stage"], "screening_assistant")
        self.assertIn("assisted_selection", report["blockers"])

    def test_a_thin_sample_cannot_reach_assisted_selection(self):
        report = assess(_state(20, [3.0] * 10), {})
        self.assertEqual(report["stage"], "screening_assistant")
        self.assertIn("僅有 10/30 個獨立決策日", report["blockers"]["assisted_selection"][0])

    def test_same_day_picks_count_as_one_decision(self):
        state = {"recommendations": [
            {"date": "2026-01-05", "outcomes": {"20": {
                "status": "complete", "poolExcessPct": value}}}
            for value in (2.0, 2.1, 1.9)
        ]}
        report = assess(state, {})
        self.assertEqual(report["versusEligiblePool"]["20"]["settled"], 1)
        self.assertEqual(report["versusEligiblePool"]["20"]["outcomes"], 3)

    def test_beating_only_the_index_is_not_enough(self):
        steady = [2.0, 2.2, 1.9, 2.1, 2.3, 1.8] * 6
        flat = [0.1, -0.1] * 18
        report = assess(_state(20, steady, versus_pool=flat), {})
        self.assertEqual(report["stage"], "screening_assistant")
        self.assertTrue(any("對合格池" in item for item in report["blockers"]["assisted_selection"]))

    def test_beating_only_the_pool_is_not_enough_either(self):
        steady = [2.0, 2.2, 1.9, 2.1, 2.3, 1.8] * 6
        losing = [-2.0, -2.2, -1.9, -2.1, -2.3, -1.8] * 6
        report = assess(_state(20, losing, versus_pool=steady), {})
        self.assertEqual(report["stage"], "screening_assistant")
        self.assertTrue(any("對 0050" in item for item in report["blockers"]["assisted_selection"]))

    def test_a_large_but_noisy_sample_is_still_blocked(self):
        noisy = [12.0, -11.0] * 20
        report = assess(_state(20, noisy), {})
        self.assertEqual(report["stage"], "screening_assistant")
        self.assertIn("信賴區間仍包含 0", report["blockers"]["assisted_selection"][0])

    def test_a_consistent_edge_over_enough_outcomes_promotes(self):
        steady = [1.8, 2.2, 2.0, 1.9, 2.1, 2.3] * 6
        self.assertEqual(assess(_state(20, steady), {})["stage"], "assisted_selection")

    def test_autonomy_also_requires_the_advice_gate(self):
        steady = [1.8, 2.2, 2.0, 1.9, 2.1, 2.3] * 12
        state = _state(60, steady)
        state["recommendations"] += _state(20, steady)["recommendations"]
        closed = assess(state, {"adviceEnabled": False})
        self.assertNotEqual(closed["stage"], "autonomous_selection")
        self.assertIn("investment-advice-gate 尚未開啟", closed["blockers"]["autonomous_selection"])
        self.assertEqual(assess(state, OPEN_GATE)["stage"], "autonomous_selection")

    def test_a_reliable_loss_never_promotes(self):
        losing = [-1.8, -2.2, -2.0, -1.9, -2.1, -2.3] * 6
        report = assess(_state(20, losing), {})
        self.assertEqual(report["stage"], "screening_assistant")
        self.assertIn("平均超額報酬不為正", report["blockers"]["assisted_selection"][0])

    def test_output_is_traditional_chinese_and_has_no_mojibake(self):
        text = render(assess(_state(20, [1.0]), {}))
        self.assertIn("目前階段", text)
        self.assertIn("獨立決策日", text)
        self.assertNotIn("�", text)
        self.assertNotIn("?", text)
        self.assertTrue(all("�" not in label for label in LABELS.values()))


if __name__ == "__main__":
    unittest.main()
