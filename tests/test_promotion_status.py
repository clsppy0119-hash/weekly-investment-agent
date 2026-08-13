"""Tests for quarantine of mutable legacy tracker outcomes."""
import unittest

from promotion_status import LABELS, assess, render


OPEN_GATE = {"adviceEnabled": True}


def _dates(count):
    return [f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(count)]


def _state(horizon, excesses, versus_pool=None):
    pool = excesses if versus_pool is None else versus_pool
    return {"recommendations": [
        {"date": day, "outcomes": {str(horizon): {
            "status": "complete", "excessReturnPct": value,
            "poolExcessPct": against_pool,
        }}}
        for day, value, against_pool in zip(_dates(len(excesses)), excesses, pool)
    ]}


class PromotionStatusTests(unittest.TestCase):
    def test_an_empty_tracker_is_research_only(self):
        self.assertEqual(assess({"recommendations": []}, {})["stage"], "research_only")

    def test_recording_candidates_remains_research_only(self):
        report = assess(_state(20, [1.0, -0.5]), {})
        self.assertEqual(report["stage"], "research_only")
        self.assertIn("assisted_selection", report["blockers"])

    def test_thin_or_noisy_legacy_samples_remain_diagnostic(self):
        thin = assess(_state(20, [3.0] * 10), {})
        self.assertEqual(thin["stage"], "research_only")
        self.assertIn("僅有 10/30 個獨立決策日", thin["blockers"]["assisted_selection"][0])
        noisy = assess(_state(20, [12.0, -11.0] * 20), {})
        self.assertEqual(noisy["stage"], "research_only")
        self.assertIn("信賴區間仍包含 0", noisy["blockers"]["assisted_selection"][0])

    def test_same_day_picks_count_as_one_diagnostic_decision(self):
        state = {"recommendations": [
            {"date": "2026-01-05", "outcomes": {"20": {
                "status": "complete", "poolExcessPct": value,
            }}}
            for value in (2.0, 2.1, 1.9)
        ]}
        report = assess(state, {})
        self.assertEqual(report["versusEligiblePool"]["20"]["settled"], 1)
        self.assertEqual(report["versusEligiblePool"]["20"]["outcomes"], 3)

    def test_forged_consistent_outcomes_and_open_gate_never_promote(self):
        steady = [1.8, 2.2, 2.0, 1.9, 2.1, 2.3] * 12
        state = _state(60, steady)
        state["recommendations"] += _state(20, steady)["recommendations"]
        report = assess(state, OPEN_GATE)
        self.assertEqual(report["stage"], "research_only")
        self.assertEqual(report["promotionEvidenceAccepted"], 0)
        self.assertGreater(report["legacyOutcomesExcluded"], 0)
        self.assertFalse(report["formalEvidenceEligible"])
        self.assertFalse(report["promotionEligible"])
        self.assertFalse(report["adviceEnabled"])
        for stage in ("assisted_selection", "autonomous_selection"):
            self.assertIn("legacy_tracker_outcomes_quarantined", report["blockers"][stage])
            self.assertIn("actual_forward_outcome_contract_not_available", report["blockers"][stage])

    def test_beating_only_one_comparator_remains_blocked(self):
        steady = [2.0, 2.2, 1.9, 2.1, 2.3, 1.8] * 6
        flat = [0.1, -0.1] * 18
        against_index = assess(_state(20, steady, versus_pool=flat), {})
        self.assertTrue(any("對合格池" in item for item in against_index["blockers"]["assisted_selection"]))
        losing = [-value for value in steady]
        against_pool = assess(_state(20, losing, versus_pool=steady), {})
        self.assertTrue(any("對 0050" in item for item in against_pool["blockers"]["assisted_selection"]))

    def test_malformed_or_nonfinite_legacy_values_fail_closed(self):
        class BadList(list):
            def __iter__(self):
                raise RuntimeError("boom")

        for state in (None, [], {"recommendations": "bad"}, {
            "recommendations": [{"outcomes": {"20": {
                "status": "complete", "excessReturnPct": float("nan"),
                "poolExcessPct": float("inf"),
            }}}],
        }, {"recommendations": BadList()}):
            report = assess(state, OPEN_GATE)
            self.assertEqual(report["stage"], "research_only")
            self.assertEqual(report["promotionEvidenceAccepted"], 0)
            self.assertFalse(report["promotionEligible"])

    def test_output_is_traditional_chinese_and_has_no_mojibake(self):
        text = render(assess(_state(20, [1.0]), {}))
        self.assertIn("目前階段", text)
        self.assertIn("獨立決策日", text)
        self.assertNotIn("�", text)
        self.assertNotIn("?", text)
        self.assertTrue(all("�" not in label for label in LABELS.values()))


if __name__ == "__main__":
    unittest.main()
