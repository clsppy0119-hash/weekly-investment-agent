import copy
import inspect
import json
import unittest
from pathlib import Path

import decision_outcome_db_contract as db
import decision_outcome_event_contract as event_contract
import decision_outcome_manifest as manifest_contract
import decision_outcome_sandbox as sandbox_contract


H = "a" * 64
SCOPE = "b" * 64


def decision(day="2026-08-12", code="2330", previous=event_contract.GENESIS):
    return event_contract.decision_candidate({
        "decisionAsOf": day, "code": code, "mode": "comprehensive", "style": "comprehensive",
        "rank": 1, "strategyVersion": "2.0", "score": 88.5, "coverage": 100,
        "entryPrice": 1200.0, "candidateManifestHash": H, "evidenceHash": H,
        "reportHash": H, "eligiblePoolHash": H, "quoteProvenanceHash": H,
        "fundamentalProvenanceHash": H, "dataQuality": "qualified",
        "costModelVersion": event_contract.COST_MODEL_VERSION,
        "claimedPreviousChainHead": previous, "researchOnly": True,
    }, enabled=True)


def snapshot(extra=False, sequence=1, previous=sandbox_contract.GENESIS):
    first = decision(); events = [first]
    if extra:
        events.append(decision(day="2026-08-13", code="2303", previous=first["eventHash"]))
    manifest = manifest_contract.freeze_manifest(events, SCOPE, enabled=True)
    material = sandbox_contract._anchor_material(manifest, sequence, previous)
    anchor = {**material, "anchorHash": sandbox_contract._digest(material)}
    return events, manifest, anchor


class DecisionOutcomeDbContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = Path(db.MIGRATION_FILE).read_text(encoding="utf-8")

    def test_default_off_has_no_payload_or_execution(self):
        self.assertIsNone(db.serialize([], {}, {})["payload"])
        self.assertFalse(db.validate_migration(self.sql)["migrationExecuted"])

    def test_serializer_is_deterministic_and_separates_transport_hash(self):
        events, manifest, anchor = snapshot()
        first = db.serialize(events, manifest, anchor, enabled=True)
        second = db.serialize(list(reversed(events)), manifest, anchor, enabled=True)
        self.assertTrue(first["mappingReady"])
        self.assertEqual(first, second)
        item = first["payload"]["p_events"][0]
        self.assertNotEqual(item["eventHash"], item["transportBlobHash"])
        self.assertEqual(set(first["payload"]), db.PAYLOAD_KEYS)
        self.assertFalse(first["promotionEligible"])

    def test_tamper_manifest_or_anchor_fails_closed(self):
        events, manifest, anchor = snapshot()
        tampered = copy.deepcopy(events); tampered[0]["payload"]["score"] = 99
        self.assertFalse(db.serialize(tampered, manifest, anchor, enabled=True)["mappingReady"])
        bad_anchor = copy.deepcopy(anchor); bad_anchor["sequence"] = 2
        self.assertFalse(db.serialize(events, manifest, bad_anchor, enabled=True)["mappingReady"])

    def test_receipt_contract_and_hash_are_exact(self):
        events, manifest, anchor = snapshot()
        receipt = {"schemaVersion": 1, "policyVersion": db.RECEIPT_POLICY,
                   "scopeId": SCOPE, "sequence": 1, "anchorHash": anchor["anchorHash"],
                   "previousAnchorHash": sandbox_contract.GENESIS,
                   "manifestDigest": manifest["manifestDigest"], "eventCount": 1,
                   "decisionDateCount": 1, "receiptHash": "", "status": "inserted",
                   "diagnosticOnly": True, "promotionEligible": False}
        receipt["receiptHash"] = db._receipt_hash(receipt)
        self.assertTrue(db.validate_receipt(receipt, enabled=True)["valid"])
        receipt["eventCount"] = 2
        self.assertFalse(db.validate_receipt(receipt, enabled=True)["valid"])

    def test_in_memory_model_duplicate_parent_and_regression(self):
        model = db.AppendRpcModel()
        events, manifest, anchor = snapshot()
        self.assertEqual(model.append(events, manifest, anchor)["status"], "inserted")
        self.assertEqual(model.append(events, manifest, anchor)["status"], "duplicate")
        more_events, more_manifest, more_anchor = snapshot(True, 2, anchor["anchorHash"])
        self.assertEqual(model.append(more_events, more_manifest, more_anchor)["status"], "inserted")
        self.assertEqual(model.append(events, manifest, anchor)["status"], "duplicate")
        wrong_events, wrong_manifest, wrong_anchor = snapshot(False, 3, more_anchor["anchorHash"])
        self.assertEqual(model.append(wrong_events, wrong_manifest, wrong_anchor)["reason"], "snapshot_regression")

    def test_model_sequence_conflict_is_atomic(self):
        model = db.AppendRpcModel(); events, manifest, anchor = snapshot()
        model.append(events, manifest, anchor)
        more_events, more_manifest, more_anchor = snapshot(True, 2, anchor["anchorHash"])
        conflict = copy.deepcopy(more_anchor); conflict["anchorHash"] = "c" * 64
        before = (copy.deepcopy(model.events), copy.deepcopy(model.manifests), copy.deepcopy(model.anchors))
        self.assertEqual(model.append(more_events, more_manifest, conflict)["status"], "blocked")
        self.assertEqual(before, (model.events, model.manifests, model.anchors))

    def test_sql_static_contract_is_pinned_private_and_append_only(self):
        result = db.validate_migration(self.sql, enabled=True)
        self.assertTrue(result["contractReady"], result["blockers"])
        self.assertFalse(result["migrationExecuted"])
        self.assertFalse(result["productionApproved"])
        lowered = self.sql.lower()
        self.assertEqual(lowered.count("force row level security"), 4)
        self.assertEqual(lowered.count("before update or delete or truncate"), 4)

    def test_sql_rejects_drift_broad_grants_and_time_aliases(self):
        cases = (self.sql + "\ngrant select on investment_decision_shadow_v1.anchor_v1 to service_role;",
                 self.sql + "\n-- available_at",
                 self.sql.replace("pg_advisory_xact_lock", "missing_lock", 1),
                 self.sql.replace("create table investment_decision_shadow_v1.audit_v1", "create table investment_decision_shadow_v1.other_v1", 1))
        for candidate in cases:
            self.assertFalse(db.validate_migration(candidate, enabled=True)["contractReady"])

    def test_output_receipt_allowlist_has_no_raw_fields(self):
        rendered = " ".join(db.RECEIPT_KEYS).lower()
        for forbidden in ("code", "price", "canonical", "url", "token", "secret", "eventtext"):
            self.assertNotIn(forbidden, rendered)

    def test_module_has_no_io_network_db_env_or_formal_imports(self):
        source = inspect.getsource(db).lower()
        for forbidden in ("open(", "pathlib", "urllib", "requests", "socket", "subprocess",
                          "psycopg", "\nimport supabase", "\nfrom supabase", "dotenv", "os.environ", "getenv(",
                          "strategy_tracker", "promotion_status", "investment_advice", "telegram"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
