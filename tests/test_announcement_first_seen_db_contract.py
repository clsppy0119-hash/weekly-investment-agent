import copy
import inspect
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import announcement_first_seen_db_contract as db
import official_announcement_first_seen as observer


def source_record(*, revision="rev-1", content="a", supersedes=None):
    fixture = {
        "provider": "TWSE", "market": "listed",
        "sourceContractId": "twse_official_announcement_detail_v1",
        "officialDocumentId": "doc-001", "officialLetterNo": "letter-001",
        "entityId": "2330", "eventType": "listing", "effectiveDate": "2026-08-13",
        "sourceRevision": revision, "contentHash": db._hash(content),
        "officialEvidence": True,
    }
    if supersedes is not None:
        fixture["supersedesContentHash"] = supersedes
    with mock.patch.object(observer, "_utc_now", return_value=datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc)):
        return observer.observe(fixture, enabled=True)["record"]


class AnnouncementFirstSeenDbContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = Path(db.MIGRATION_FILE).read_text(encoding="utf-8")

    def test_default_off_never_serializes_or_validates(self):
        self.assertEqual(db.serialize({})["mode"], "disabled")
        self.assertEqual(db.validate_migration("")["mode"], "disabled")
        self.assertEqual(db.privilege_model()["mode"], "disabled")

    def test_serializer_is_exact_snake_case_forward_only(self):
        result = db.serialize(source_record(), enabled=True)
        self.assertTrue(result["mappingReady"])
        row = result["row"]
        self.assertEqual(tuple(row), db.ROW_COLUMNS)
        self.assertEqual(row["evidence_mode"], "forward_observed_only")
        self.assertEqual(row["visibility"], "private_lineage")
        self.assertEqual(row["first_seen_at"], "2026-08-12T09:30:00.000000Z")
        self.assertNotIn("available_at", row)
        self.assertNotIn("published_at", row)
        self.assertEqual(row["metadata"], db.METADATA)

    def test_serializer_rejects_tamper_or_historical_alias(self):
        for change in ({"recordHash": "0" * 64}, {"availableAt": "2020-01-01T00:00:00Z"},
                       {"historicalEligible": True}, {"classification": "HISTORICAL_CERTIFIABLE"}):
            record = source_record()
            record.update(change)
            self.assertFalse(db.serialize(record, enabled=True)["mappingReady"])

    def test_rpc_model_duplicate_conflict_and_exact_correction(self):
        first_source = source_record(content="first")
        first = db.serialize(first_source, enabled=True)["row"]
        model = db.AppendRpcModel()
        self.assertEqual(model.append(first)["status"], "inserted")
        self.assertEqual(model.append(copy.deepcopy(first))["status"], "duplicate")
        changed = copy.deepcopy(first)
        changed["first_seen_at"] = "2026-08-12T09:31:00.000000Z"
        self.assertEqual(model.append(changed)["status"], "blocked")
        missing = db.serialize(source_record(revision="rev-2", content="second"), enabled=True)["row"]
        self.assertEqual(model.append(missing)["reason"], "correction_conflict")
        corrected_source = source_record(revision="rev-2", content="second", supersedes=first["content_hash"])
        corrected = db.serialize(corrected_source, enabled=True)["row"]
        self.assertEqual(model.append(corrected)["status"], "inserted")

    def test_unknown_supersedes_fails_closed(self):
        record = source_record(supersedes="1" * 64)
        row = db.serialize(record, enabled=True)["row"]
        self.assertEqual(db.AppendRpcModel().append(row)["reason"], "unknown_supersedes")

    def test_pinned_migration_contract_passes_but_is_never_executed(self):
        result = db.validate_migration(self.sql, enabled=True)
        self.assertTrue(result["contractReady"], result["blockers"])
        self.assertFalse(result["migrationExecuted"])
        self.assertFalse(result["serviceRoleSafe"])
        self.assertEqual(result["migrationHash"], db.PINNED_MIGRATION_SHA256)
        self.assertIn("service_role_bypass_not_approved", result["blockers"])

    def test_parser_rejects_unpinned_destructive_foreign_or_alias_drift(self):
        variants = (
            self.sql + "\ndrop table public.anything;",
            self.sql + "\ncreate table if not exists public.foreign_table (id int);",
            self.sql + "\n-- available_at must not appear",
            self.sql.replace(" force row level security;", ";", 1),
        )
        expected = (
            "destructive_statement_forbidden", "foreign_or_missing_table_change",
            "historical_availability_alias_forbidden", "required_guard_missing",
        )
        for sql, marker in zip(variants, expected):
            with self.subTest(marker=marker):
                result = db.validate_migration(sql, enabled=True)
                self.assertFalse(result["contractReady"])
                self.assertTrue(any(marker in blocker for blocker in result["blockers"]))
                self.assertIn("migration_hash_unpinned_or_drifted", result["blockers"])

    def test_privilege_model_denies_direct_access_and_service_role(self):
        result = db.privilege_model(enabled=True)
        for role in ("public", "anon", "authenticated", "lineage_observer_writer"):
            self.assertTrue(all(value is False for value in result["directTablePrivileges"][role].values()))
        self.assertTrue(result["rpcExecute"]["lineage_observer_writer"])
        self.assertFalse(result["rpcExecute"]["service_role"])
        self.assertTrue(result["forceRls"])
        self.assertTrue(result["updateDeleteTriggerAllCallers"])
        self.assertTrue(result["serviceRoleBypassesRls"])
        self.assertFalse(result["serviceRoleSafeForRoutineProducer"])

    def test_sql_is_isolated_append_only_and_has_no_existing_table_change(self):
        lowered = self.sql.lower()
        self.assertNotIn("investment_data_lineage_shadow", lowered)
        self.assertNotIn("available_at", lowered)
        self.assertIn("before update or delete", lowered)
        self.assertIn("force row level security", lowered)
        self.assertIn("p_provider, p_official_document_id, p_entity_id", lowered)
        self.assertIn("revoke all", lowered)
        self.assertNotRegex(lowered, r"(?m)^\s*(drop|truncate|update|delete)\b")

    def test_module_has_no_db_network_secret_or_formal_imports(self):
        source = inspect.getsource(db).lower()
        for forbidden in ("urllib", "requests", "socket", "subprocess", "dotenv",
                          "os.environ", "psycopg", "lineage_writer", "candidate_manifest",
                          "backtest", "telegram"):
            self.assertNotIn(forbidden, source)
        self.assertNotRegex(source, r"(?:from|import)\s+supabase")


if __name__ == "__main__":
    unittest.main()
