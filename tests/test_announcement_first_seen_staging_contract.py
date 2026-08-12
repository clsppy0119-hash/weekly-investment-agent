import copy
import inspect
import unittest
from pathlib import Path

import announcement_first_seen_db_contract as db
import announcement_first_seen_staging_contract as staging


def valid_row():
    row = {
        "provider": "TWSE", "market": "listed", "source_contract_id": "official-v1",
        "official_document_id": "doc-1", "official_letter_no": "letter-1", "entity_id": "2330",
        "event_type": "listing", "effective_date": "2026-08-13", "source_revision": "rev-1",
        "content_hash": db._hash("body"), "first_seen_at": "2026-08-12T09:30:00.000000Z",
        "supersedes_content_hash": None, "composite_key": "", "record_hash": "",
        "schema_version": 1, "policy_version": db.POLICY_VERSION,
        "evidence_mode": "forward_observed_only", "visibility": "private_lineage",
        "metadata": copy.deepcopy(db.METADATA),
    }
    row["composite_key"] = db._delimited_hash([
        row["provider"], row["source_contract_id"], row["official_document_id"], row["entity_id"],
        row["event_type"], row["effective_date"], row["source_revision"], row["schema_version"],
    ])
    row["record_hash"] = db._delimited_hash([
        row["provider"], row["market"], row["source_contract_id"], row["official_document_id"],
        row["official_letter_no"], row["entity_id"], row["event_type"], row["effective_date"],
        row["source_revision"], row["content_hash"], row["first_seen_at"], row["supersedes_content_hash"],
        row["composite_key"], row["schema_version"], row["policy_version"],
        row["evidence_mode"], row["visibility"],
    ])
    return row


def valid_summary():
    return {
        "schemaVersion": 1, "environment": "staging", "stagingRefHash": "1" * 64,
        "productionRefHash": "2" * 64, "mainSha": staging.MAIN_SHA,
        "b2a2SqlPin": db.PINNED_MIGRATION_SHA256,
        "b2a2SemanticPin": db.PINNED_SEMANTIC_CONTRACT_SHA256,
        "executorRoleIsPostgres": True, "transactionReadOnly": True,
        "pgcryptoNamespaceExact": True, "digestSignaturePresent": True,
        "targetRoleCount": 0, "targetSchemaCount": 0, "targetRelationCount": 0,
        "targetRoutineCount": 0, "dashboardExposureVerified": True,
        "privateSchemaInDashboardExposed": False, "authenticatorOverrideAbsentOrSafe": True,
        "privateRuntimeGrantCount": 0, "privateViewExposureCount": 0,
        "privateRoutineExposureCount": 0, "privatePublicationExposureCount": 0,
        "formalIsolationVerified": True,
    }


class B2B1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator_sql = Path(staging.VALIDATOR_SQL_FILE).read_text(encoding="utf-8")
        cls.preflight_sql = Path(staging.PREFLIGHT_SQL_FILE).read_text(encoding="utf-8")

    def test_default_off(self):
        self.assertEqual(staging.validate_payload({})["mode"], "disabled")
        self.assertEqual(staging.validate_validator_sql("")["mode"], "disabled")
        self.assertEqual(staging.validate_preflight_sql("")["mode"], "disabled")
        self.assertEqual(staging.evaluate_preflight({})["mode"], "disabled")

    def test_payload_validates_and_output_is_bounded(self):
        result = staging.validate_payload(valid_row(), enabled=True)
        self.assertTrue(result["valid"])
        self.assertEqual(set(result), {"mode", "diagnosticOnly", "valid", "status", "recordHash", "blockers"})
        self.assertNotIn("firstSeen", str(result))

    def test_payload_tamper_and_aliases_fail_closed(self):
        changes = ({"record_hash": "0" * 64}, {"composite_key": "0" * 64},
                   {"metadata": {}}, {"available_at": "2020-01-01T00:00:00Z"})
        for change in changes:
            row = valid_row(); row.update(change)
            self.assertFalse(staging.validate_payload(row, enabled=True)["valid"])

    def test_validator_sql_is_pinned_pure_and_writer_only(self):
        result = staging.validate_validator_sql(self.validator_sql, enabled=True)
        self.assertTrue(result["contractReady"], result["blockers"])
        self.assertFalse(result["migrationExecuted"])
        for mutation in (
            self.validator_sql + "\nselect * from public.anything;",
            self.validator_sql.replace("set search_path = pg_catalog", "set search_path = public"),
            self.validator_sql.replace("extensions.digest", "public.digest"),
            self.validator_sql + "\ngrant execute on function public.validate_announcement_first_seen_payload_v1(text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb) to authenticated;",
        ):
            self.assertFalse(staging.validate_validator_sql(mutation, enabled=True)["contractReady"])

    def test_preflight_sql_is_pinned_catalog_only_read_only(self):
        result = staging.validate_preflight_sql(self.preflight_sql, enabled=True)
        self.assertTrue(result["contractReady"], result["blockers"])
        self.assertFalse(result["executed"])
        for mutation in (
            self.preflight_sql + "\ninsert into public.anything values (1);",
            self.preflight_sql + "\nselect * from public.investment_announcement_first_seen_shadow_v1;",
            self.preflight_sql.replace("begin transaction read only;", "begin;"),
            self.preflight_sql + "\nselect now();",
        ):
            self.assertFalse(staging.validate_preflight_sql(mutation, enabled=True)["contractReady"])

    def test_summary_passes_only_clean_separated_staging(self):
        result = staging.evaluate_preflight(valid_summary(), enabled=True)
        self.assertTrue(result["ready"], result["blockers"])
        self.assertEqual(result["mode"], "research_only")
        self.assertNotIn("stagingRefHash", result)
        for field, value in (("productionRefHash", "1" * 64), ("targetRoleCount", 1),
                             ("dashboardExposureVerified", False),
                             ("privateSchemaInDashboardExposed", True),
                             ("mainSha", "0" * 40)):
            summary = valid_summary(); summary[field] = value
            self.assertFalse(staging.evaluate_preflight(summary, enabled=True)["ready"])

    def test_unknown_fields_and_raw_identifiers_are_not_emitted(self):
        summary = valid_summary(); summary["projectRef"] = "raw-project-ref"
        result = staging.evaluate_preflight(summary, enabled=True)
        self.assertFalse(result["ready"])
        self.assertNotIn("raw-project-ref", str(result))

    def test_module_has_no_io_secret_clock_or_formal_flow_imports(self):
        source = inspect.getsource(staging).lower()
        for forbidden in ("urllib", "requests", "socket", "subprocess", "dotenv", "os.environ",
                          "psycopg", "time.time", "datetime.now", "candidate_manifest",
                          "backtest", "telegram", "investment_advice"):
            self.assertNotIn(forbidden, source)
        self.assertNotRegex(source, r"(?:from|import)\s+supabase")


if __name__ == "__main__":
    unittest.main()
