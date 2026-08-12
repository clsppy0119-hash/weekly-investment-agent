import copy
import inspect
import math
import unittest

import decision_outcome_event_contract as contract


H = "a" * 64


def decision():
    return {
        "decisionAsOf": "2026-08-12", "code": "2330", "mode": "comprehensive",
        "style": "comprehensive", "rank": 1, "strategyVersion": "2.0",
        "score": 88.5, "coverage": 100, "entryPrice": 1200.0,
        "candidateManifestHash": H, "evidenceHash": H, "reportHash": H,
        "eligiblePoolHash": H, "quoteProvenanceHash": H,
        "fundamentalProvenanceHash": H, "dataQuality": "qualified",
        "costModelVersion": contract.COST_MODEL_VERSION,
        "claimedPreviousChainHead": contract.GENESIS, "researchOnly": True,
    }


def outcome():
    return {
        "decisionEventHash": H, "horizon": 20, "settledDate": "2026-09-09",
        "netReturnPct": 5.0, "totalReturnNetPct": 5.5,
        "benchmarkNetReturnPct": 2.0, "poolNetReturnPct": 3.0,
        "excessReturnPct": 3.5, "poolExcessPct": 2.0,
        "costModelVersion": contract.COST_MODEL_VERSION,
        "corporateActionEvidenceHash": H, "priceSnapshotHash": H,
        "benchmarkArtifactHash": H, "poolArtifactHash": H,
        "pitCoverage": 100, "sourceStatus": "qualified",
        "claimedPreviousOutcomeHash": contract.GENESIS, "researchOnly": True,
    }


class EventCandidateContractTests(unittest.TestCase):
    def test_default_off_has_no_output(self):
        self.assertIsNone(contract.decision_candidate(decision()))
        self.assertIsNone(contract.outcome_candidate(outcome()))
        self.assertIsNone(contract.legacy_candidate({}))

    def test_decision_is_diagnostic_never_promotable_or_chain_verified(self):
        event = contract.decision_candidate(decision(), enabled=True)
        self.assertEqual(event["logicalKey"], "2026-08-12:comprehensive:comprehensive:2330")
        self.assertTrue(event["diagnosticOnly"])
        self.assertFalse(event["promotionEligible"])
        self.assertFalse(event["chainVerified"])
        material = {key: value for key, value in event.items() if key != "eventHash"}
        self.assertEqual(event["eventHash"], contract._digest(material))

    def test_input_is_deep_copied(self):
        payload = decision()
        event = contract.decision_candidate(payload, enabled=True)
        payload["code"] = "9999"
        self.assertEqual(event["payload"]["code"], "2330")

    def test_schema_non_dict_sensitive_and_nested_sensitive_fail_bounded(self):
        for payload in (None, [], {"x": 1}):
            with self.assertRaisesRegex(ValueError, "event_schema_mismatch"):
                contract.decision_candidate(payload, enabled=True)
        payload = decision(); payload["mode"] = "https://unsafe.invalid"
        with self.assertRaisesRegex(ValueError, "event_sensitive_content"):
            contract.decision_candidate(payload, enabled=True)

    def test_each_decision_type_and_range_is_checked(self):
        mutations = {
            "decisionAsOf": "2026-02-30", "code": "ABC", "mode": "bad mode",
            "style": "", "rank": True, "strategyVersion": "", "score": math.nan,
            "coverage": 101, "entryPrice": 0, "dataQuality": "passed",
            "costModelVersion": "changed", "researchOnly": False,
        }
        for field, value in mutations.items():
            payload = decision(); payload[field] = value
            expected = "event_value_not_canonical" if field == "score" else "decision_policy_invalid"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, expected):
                contract.decision_candidate(payload, enabled=True)

    def test_each_hash_requires_lower_hex(self):
        for field in ("candidateManifestHash", "evidenceHash", "reportHash",
                      "eligiblePoolHash", "quoteProvenanceHash",
                      "fundamentalProvenanceHash", "claimedPreviousChainHead"):
            payload = decision(); payload[field] = "G" * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "event_hash_invalid"):
                contract.decision_candidate(payload, enabled=True)

    def test_outcome_requires_complete_sources_and_exact_arithmetic(self):
        event = contract.outcome_candidate(outcome(), enabled=True)
        self.assertEqual(event["logicalKey"], f"outcome:{H}:20")
        self.assertFalse(event["promotionEligible"])
        for field, value in (("pitCoverage", 99), ("sourceStatus", "unknown"),
                             ("horizon", 10), ("settledDate", "bad"),
                             ("netReturnPct", math.inf), ("researchOnly", False),
                             ("costModelVersion", "v2")):
            payload = outcome(); payload[field] = value
            expected = "event_value_not_canonical" if field == "netReturnPct" else "outcome_provenance_incomplete"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, expected):
                contract.outcome_candidate(payload, enabled=True)
        payload = outcome(); payload["excessReturnPct"] = 99
        with self.assertRaisesRegex(ValueError, "outcome_arithmetic_invalid"):
            contract.outcome_candidate(payload, enabled=True)

    def test_outcome_hashes_are_mandatory(self):
        for field in ("decisionEventHash", "corporateActionEvidenceHash",
                      "priceSnapshotHash", "benchmarkArtifactHash",
                      "poolArtifactHash", "claimedPreviousOutcomeHash"):
            payload = outcome(); payload[field] = None
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "event_hash_invalid"):
                contract.outcome_candidate(payload, enabled=True)

    def test_legacy_is_fixed_and_never_promotable(self):
        payload = {"legacySourceHash": H, "recordCount": 15,
                   "reason": "mutable_state_without_original_event_chain",
                   "researchOnly": True}
        event = contract.legacy_candidate(payload, enabled=True)
        self.assertFalse(event["promotionEligible"])
        for field, value in (("recordCount", 0), ("reason", "trusted"),
                             ("researchOnly", False)):
            changed = copy.deepcopy(payload); changed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "legacy_policy_invalid"):
                contract.legacy_candidate(changed, enabled=True)

    def test_module_has_no_io_network_env_or_formal_flow_imports(self):
        source = inspect.getsource(contract).lower()
        for forbidden in ("open(", "pathlib", "os.environ", "getenv(", "requests",
                          "urllib", "socket", "subprocess", "psycopg", "supabase",
                          "candidate_manifest", "strategy_tracker", "promotion_status",
                          "investment_advice", "telegram"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
