import unittest

from investment_agent.shadow_committee import run_shadow_committee


class ShadowCommitteeTests(unittest.TestCase):
    def test_default_off_is_side_effect_free(self):
        result = run_shadow_committee({"quality": {"passed": True}})
        self.assertEqual(result["status"], "disabled")
        self.assertTrue(result["shadow_only"])
        self.assertTrue(result["production_unchanged"])

    def test_positive_verdict_requires_independent_bear(self):
        packet = {
            "quality": {"passed": True},
            "evidence": [
                {"id": "g1", "category": "growth"},
                {"id": "v1", "category": "valuation"},
                {"id": "d1", "category": "demand_earnings"},
                {"id": "s1", "category": "geopolitical_supply_chain"},
            ],
            "falsification_triggers": ["revenue_yoy_below_zero"],
        }
        result = run_shadow_committee(packet, enabled=True)
        self.assertEqual(result["judge"]["verdict"], "buy_candidate")
        self.assertTrue(result["judge"]["bear_independence_passed"])

    def test_missing_bear_challenge_blocks_positive_verdict(self):
        packet = {"quality": {"passed": True}, "evidence": [{"id": "g1", "category": "growth"}]}
        result = run_shadow_committee(packet, enabled=True)
        self.assertEqual(result["judge"]["verdict"], "observe")
        self.assertFalse(result["judge"]["bear_independence_passed"])


if __name__ == "__main__":
    unittest.main()
