import copy
import inspect
import unittest
from pathlib import Path

import decision_outcome_event_contract as event_contract
import decision_outcome_manifest as manifest_contract
import decision_outcome_sandbox as sandbox_contract
import decision_outcome_staging_dry_run as dry_run


H = "a" * 64
SCOPE = "b" * 64


def fixture():
    event = event_contract.decision_candidate({
        "decisionAsOf": "2026-08-12", "code": "2330", "mode": "comprehensive",
        "style": "comprehensive", "rank": 1, "strategyVersion": "2.0",
        "score": 88.5, "coverage": 100, "entryPrice": 1200.0,
        "candidateManifestHash": H, "evidenceHash": H, "reportHash": H,
        "eligiblePoolHash": H, "quoteProvenanceHash": H,
        "fundamentalProvenanceHash": H, "dataQuality": "qualified",
        "costModelVersion": event_contract.COST_MODEL_VERSION,
        "claimedPreviousChainHead": event_contract.GENESIS, "researchOnly": True,
    }, enabled=True)
    events = [event]
    manifest = manifest_contract.freeze_manifest(events, SCOPE, enabled=True)
    material = sandbox_contract._anchor_material(manifest, 1, sandbox_contract.GENESIS)
    anchor = {**material, "anchorHash": sandbox_contract._digest(material)}
    return events, manifest, anchor


class OutcomeStagingDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.migration = Path("supabase-decision-outcome-shadow-v1.sql").read_text(encoding="utf-8")

    def test_default_off_emits_no_sql(self):
        self.assertIsNone(dry_run.build("", [], {}, {})["sql"])
        self.assertFalse(dry_run.validate("", "")["valid"])

    def test_bundle_is_deterministic_pinned_and_valid(self):
        events, manifest, anchor = fixture()
        first = dry_run.build(self.migration, events, manifest, anchor, enabled=True)
        second = dry_run.build(self.migration, copy.deepcopy(events), copy.deepcopy(manifest),
                               copy.deepcopy(anchor), enabled=True)
        self.assertTrue(first["ready"])
        self.assertEqual(first, second)
        checked = dry_run.validate(first["sql"], self.migration, enabled=True)
        self.assertTrue(checked["valid"], checked["blockers"])
        self.assertEqual(checked["sqlHash"], first["sqlHash"])
        self.assertFalse(first["productionApproved"])

    def test_tampered_fixture_or_migration_fails_closed(self):
        events, manifest, anchor = fixture()
        bad = copy.deepcopy(events); bad[0]["payload"]["score"] = 99
        self.assertFalse(dry_run.build(self.migration, bad, manifest, anchor, enabled=True)["ready"])
        drift = self.migration + "\n-- drift"
        self.assertFalse(dry_run.build(drift, events, manifest, anchor, enabled=True)["ready"])

    def test_transaction_and_rollback_boundaries_are_exact(self):
        events, manifest, anchor = fixture()
        sql = dry_run.build(self.migration, events, manifest, anchor, enabled=True)["sql"]
        self.assertEqual(sql.lower().count("rollback;"), 2)
        self.assertTrue(sql.rstrip().endswith("rollback;"))
        self.assertNotIn("commit;", sql.lower())
        for mutation in (
            sql.replace("rollback;", "commit;", 1),
            sql.rsplit("rollback;", 1)[0],
            sql.replace("begin transaction read only;", "begin;", 1),
            sql.replace("decision_outcome_sequence_conflict", "ignored_conflict", 1),
        ):
            self.assertFalse(dry_run.validate(mutation, self.migration, enabled=True)["valid"])

    def test_permissions_duplicate_conflict_and_zero_residue_are_guarded(self):
        events, manifest, anchor = fixture()
        sql = dry_run.build(self.migration, events, manifest, anchor, enabled=True)["sql"].lower()
        for required in (
            "when insufficient_privilege", "status' <> 'inserted'", "status' <> 'duplicate'",
            "decision_outcome_sequence_conflict", "audit_v1) <> 2",
            "pg_catalog.to_regrole('decision_outcome_owner') is null",
            "pg_catalog.to_regnamespace('investment_decision_shadow_v1') is null",
            "'productionapproved', false",
        ):
            self.assertIn(required, sql)

    def test_postgresql_creator_memberships_are_removed_before_migration(self):
        events, manifest, anchor = fixture()
        sql = dry_run.build(self.migration, events, manifest, anchor, enabled=True)["sql"].lower()
        owner_revoke = "revoke decision_outcome_owner from postgres;"
        writer_revoke = "revoke decision_outcome_writer from postgres;"
        migration_preflight = "do $preflight$"
        self.assertIn(owner_revoke, sql)
        self.assertIn(writer_revoke, sql)
        self.assertLess(sql.index(owner_revoke), sql.index(migration_preflight))
        self.assertLess(sql.index(writer_revoke), sql.index(migration_preflight))
        self.assertNotIn("grant decision_outcome_owner", sql)
        self.assertNotIn("grant decision_outcome_writer", sql)
        self.assertFalse(dry_run.validate(
            sql.replace(owner_revoke, "-- missing owner membership cleanup"),
            self.migration, enabled=True,
        )["valid"])
        self.assertFalse(dry_run.validate(
            sql.replace(writer_revoke, "-- missing writer membership cleanup"),
            self.migration, enabled=True,
        )["valid"])

    def test_output_has_no_credentials_urls_or_formal_flow(self):
        events, manifest, anchor = fixture()
        sql = dry_run.build(self.migration, events, manifest, anchor, enabled=True)["sql"].lower()
        for forbidden in ("supabase_url", "service_role_key", "postgresql://", "https://",
                          "telegram", "strategy_tracker", "promotion_status", "investment_advice"):
            self.assertNotIn(forbidden, sql)

    def test_module_has_no_io_network_env_db_or_execution(self):
        source = inspect.getsource(dry_run).lower()
        for forbidden in ("open(", "pathlib", "requests", "urllib", "socket", "subprocess",
                          "psycopg", "\nimport supabase", "\nfrom supabase", "os.environ",
                          "getenv(", "execute("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
