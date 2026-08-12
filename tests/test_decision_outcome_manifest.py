import copy
import inspect
import unittest

import decision_outcome_event_contract as event_contract
import decision_outcome_manifest as manifest_contract


H = "a" * 64
SCOPE = "b" * 64


def decision(day="2026-08-12", code="2330", previous=event_contract.GENESIS):
    payload = {
        "decisionAsOf": day, "code": code, "mode": "comprehensive",
        "style": "comprehensive", "rank": 1, "strategyVersion": "2.0",
        "score": 88.5, "coverage": 100, "entryPrice": 1200.0,
        "candidateManifestHash": H, "evidenceHash": H, "reportHash": H,
        "eligiblePoolHash": H, "quoteProvenanceHash": H,
        "fundamentalProvenanceHash": H, "dataQuality": "qualified",
        "costModelVersion": event_contract.COST_MODEL_VERSION,
        "claimedPreviousChainHead": previous, "researchOnly": True,
    }
    return event_contract.decision_candidate(payload, enabled=True)


def outcome(parent, horizon=5, previous=event_contract.GENESIS, settled="2026-08-20"):
    payload = {
        "decisionEventHash": parent, "horizon": horizon, "settledDate": settled,
        "netReturnPct": 5.0, "totalReturnNetPct": 5.5,
        "benchmarkNetReturnPct": 2.0, "poolNetReturnPct": 3.0,
        "excessReturnPct": 3.5, "poolExcessPct": 2.0,
        "costModelVersion": event_contract.COST_MODEL_VERSION,
        "corporateActionEvidenceHash": H, "priceSnapshotHash": H,
        "benchmarkArtifactHash": H, "poolArtifactHash": H,
        "pitCoverage": 100, "sourceStatus": "qualified",
        "claimedPreviousOutcomeHash": previous, "researchOnly": True,
    }
    return event_contract.outcome_candidate(payload, enabled=True)


def valid_set():
    legacy = event_contract.legacy_candidate({
        "legacySourceHash": H, "recordCount": 15,
        "reason": "mutable_state_without_original_event_chain", "researchOnly": True,
    }, enabled=True)
    # decision logical-key order puts 2330 after 2303; rebuild chain accordingly.
    second = decision(code="2303")
    first = decision(previous=second["eventHash"])
    result5 = outcome(first["eventHash"])
    result20 = outcome(first["eventHash"], 20, result5["eventHash"], "2026-09-15")
    return [result20, legacy, first, result5, second]


class FrozenManifestTests(unittest.TestCase):
    def test_default_off(self):
        self.assertIsNone(manifest_contract.freeze_manifest([], SCOPE))
        self.assertFalse(manifest_contract.verify([], {}, enabled=False)["verifiedCandidateSet"])

    def test_valid_set_replays_deterministically_and_is_not_anchored(self):
        events = valid_set()
        frozen = manifest_contract.freeze_manifest(events, SCOPE, enabled=True)
        first = manifest_contract.verify(events, frozen, enabled=True)
        second = manifest_contract.verify(list(reversed(events)), frozen, enabled=True)
        self.assertEqual(first, second)
        self.assertTrue(first["verifiedCandidateSet"])
        self.assertTrue(first["readyForWriterReview"])
        self.assertFalse(first["completenessExternallyAnchored"])
        self.assertFalse(first["promotionEligible"])
        self.assertEqual(first["decisionCount"], 2)
        self.assertEqual(first["decisionDateCount"], 1)
        self.assertEqual(first["legacyCount"], 1)

    def test_manifest_detects_missing_added_replaced_unsorted_and_counts(self):
        events = valid_set(); frozen = manifest_contract.freeze_manifest(events, SCOPE, enabled=True)
        cases = []
        cases.append((events[:-1], frozen))
        added = events + [decision(day="2026-08-13", code="2317", previous=events[2]["eventHash"])]
        cases.append((added, frozen))
        unsorted = copy.deepcopy(frozen); unsorted["eventHashes"] = list(reversed(unsorted["eventHashes"]))
        cases.append((events, unsorted))
        counted = copy.deepcopy(frozen); counted["expectedDecisionCount"] += 1
        cases.append((events, counted))
        digested = copy.deepcopy(frozen); digested["manifestDigest"] = H
        cases.append((events, digested))
        for candidate_events, candidate_manifest in cases:
            self.assertFalse(manifest_contract.verify(candidate_events, candidate_manifest, enabled=True)["verifiedCandidateSet"])

    def test_event_tamper_duplicate_hash_and_logical_key_fail(self):
        events = valid_set(); frozen = manifest_contract.freeze_manifest(events, SCOPE, enabled=True)
        tampered = copy.deepcopy(events); tampered[0]["payload"]["horizon"] = 60
        self.assertFalse(manifest_contract.verify(tampered, frozen, enabled=True)["verifiedCandidateSet"])
        duplicated = events + [copy.deepcopy(events[0])]
        duplicate_manifest = copy.deepcopy(frozen)
        self.assertFalse(manifest_contract.verify(duplicated, duplicate_manifest, enabled=True)["verifiedCandidateSet"])

    def test_orphan_parent_bad_chain_and_early_settlement_fail(self):
        events = valid_set()
        orphan = copy.deepcopy(events); orphan[0] = outcome("c" * 64, 20, events[3]["eventHash"], "2026-09-15")
        bad_chain = copy.deepcopy(events); bad_chain[2] = decision(previous=event_contract.GENESIS)
        early = copy.deepcopy(events); early[3] = outcome(events[2]["eventHash"], 5, settled="2026-08-11")
        for candidate in (orphan, bad_chain, early):
            frozen = manifest_contract.freeze_manifest(candidate, SCOPE, enabled=True)
            self.assertFalse(manifest_contract.verify(candidate, frozen, enabled=True)["verifiedCandidateSet"])

    def test_duplicate_outcome_identity_and_reversed_horizon_date_fail(self):
        events = valid_set()
        duplicate = events + [copy.deepcopy(events[3])]
        with self.assertRaisesRegex(ValueError, "duplicate_event_hash"):
            manifest_contract.freeze_manifest(duplicate, SCOPE, enabled=True)
        reversed_dates = copy.deepcopy(events)
        reversed_dates[0] = outcome(events[2]["eventHash"], 20, events[3]["eventHash"], "2026-08-19")
        frozen = manifest_contract.freeze_manifest(reversed_dates, SCOPE, enabled=True)
        self.assertFalse(manifest_contract.verify(reversed_dates, frozen, enabled=True)["verifiedCandidateSet"])

    def test_more_than_one_legacy_summary_is_rejected(self):
        events = valid_set()
        another = event_contract.legacy_candidate({
            "legacySourceHash": "c" * 64, "recordCount": 1,
            "reason": "mutable_state_without_original_event_chain", "researchOnly": True,
        }, enabled=True)
        with self.assertRaisesRegex(ValueError, "multiple_legacy_summaries_forbidden"):
            manifest_contract.freeze_manifest(events + [another], SCOPE, enabled=True)

    def test_output_is_sanitized_and_exact(self):
        events = valid_set(); frozen = manifest_contract.freeze_manifest(events, SCOPE, enabled=True)
        result = manifest_contract.verify(events, frozen, enabled=True)
        self.assertEqual(set(result), manifest_contract.OUTPUT_KEYS)
        rendered = str(result)
        self.assertNotIn("2330", rendered)
        self.assertNotIn("2026-08-12", rendered)

    def test_module_has_no_io_network_env_or_formal_imports(self):
        source = inspect.getsource(manifest_contract).lower()
        for forbidden in ("open(", "pathlib", "os.environ", "getenv(", "requests",
                          "urllib", "socket", "subprocess", "psycopg", "supabase",
                          "strategy_tracker", "promotion_status", "investment_advice", "telegram"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
