import copy
import inspect
import unittest

import decision_outcome_event_contract as event_contract
import decision_outcome_export as exporter


H = "a" * 64


def manifest():
    records = [
        {"name": name, "quality": "verified", "conflictStatus": "no_conflict",
         "availableAt": "2026-08-12T08:00:00+08:00", "evidenceHash": char * 64}
        for name, char in (("quote", "1"), ("fundamentals", "2"),
                           ("corporate_actions", "3"), ("point_in_time", "4"))
    ]
    previews = [
        {"code": "2330", "name": "台積電", "style": "comprehensive", "rank": 1,
         "score": 88.5, "coverage": 100, "entryPrice": 1200.0,
         "quality": {"passed": True, "blockers": []}},
        {"code": "2317", "name": "鴻海", "style": "comprehensive", "rank": 2,
         "score": 80.0, "coverage": 75, "entryPrice": 180.0,
         "quality": {"passed": False, "blockers": ["analysis_coverage_below_80"]}},
    ]
    value = {
        "schemaVersion": 1, "reportDate": "2026-08-12", "reportMode": "comprehensive",
        "phase": "final", "strategyVersion": "2.0", "quoteUpdatedAt": "2026-08-12",
        "adviceGate": {"status": "research_only", "adviceEnabled": False, "blockers": ["test"]},
        "candidateOrder": ["2330", "2317"], "previewCandidates": previews,
        "eligibleCandidates": [], "evidenceInputs": {},
        "dataContract": {"schemaVersion": 1, "certified": True, "blockers": [],
                         "contractHash": exporter._digest(records), "records": records},
    }
    return value


class DecisionOutcomeExportTests(unittest.TestCase):
    def test_default_off_emits_no_events(self):
        output = exporter.export({}, "", H)
        self.assertEqual(output["mode"], "disabled")
        self.assertEqual(output["events"], [])

    def test_research_only_gate_still_exports_quality_passed_shadow_candidate(self):
        output = exporter.export(manifest(), "研究報告", event_contract.GENESIS, enabled=True)
        self.assertTrue(output["readyForWriterReview"], output["blockers"])
        self.assertEqual(output["eventCount"], 1)
        self.assertEqual(output["events"][0]["payload"]["code"], "2330")
        self.assertTrue(output["events"][0]["payload"]["researchOnly"])
        self.assertFalse(output["promotionEligible"])
        self.assertEqual(output["nextChainHead"], output["events"][0]["eventHash"])

    def test_export_is_deterministic_and_chains_multiple_candidates(self):
        value = manifest()
        second = copy.deepcopy(value["previewCandidates"][0])
        second.update({"code": "2454", "name": "聯發科", "rank": 2, "entryPrice": 1400.0})
        value["previewCandidates"] = [value["previewCandidates"][0], second]
        value["candidateOrder"] = ["2330", "2454"]
        first = exporter.export(value, "研究報告", H, enabled=True)
        again = exporter.export(copy.deepcopy(value), "研究報告", H, enabled=True)
        self.assertEqual(first, again)
        self.assertEqual(first["eventCount"], 2)
        self.assertEqual(first["events"][1]["payload"]["claimedPreviousChainHead"],
                         first["events"][0]["eventHash"])

    def test_uncertified_or_incomplete_provenance_fails_closed(self):
        for mutation in ("certified", "availableAt", "conflictStatus"):
            value = manifest()
            if mutation == "certified":
                value["dataContract"]["certified"] = False
            elif mutation == "availableAt":
                value["dataContract"]["records"][0]["availableAt"] = None
            else:
                value["dataContract"]["records"][0]["conflictStatus"] = "conflict"
            value["dataContract"]["contractHash"] = exporter._digest(
                value["dataContract"]["records"]
            )
            output = exporter.export(value, "研究報告", H, enabled=True)
            self.assertFalse(output["readyForWriterReview"])
            self.assertEqual(output["events"], [])

    def test_data_contract_hash_must_bind_exact_records(self):
        value = manifest()
        value["dataContract"]["records"][0]["evidenceHash"] = "f" * 64
        output = exporter.export(value, "研究報告", H, enabled=True)
        self.assertFalse(output["readyForWriterReview"])
        self.assertIn("data_contract_hash_mismatch", output["blockers"])

    def test_order_price_and_quality_are_strict(self):
        mutations = []
        value = manifest(); value["candidateOrder"].reverse(); mutations.append(value)
        value = manifest(); value["previewCandidates"][0]["entryPrice"] = 0; mutations.append(value)
        value = manifest(); value["previewCandidates"][0]["quality"]["blockers"] = ["x"]; mutations.append(value)
        for value in mutations:
            self.assertFalse(exporter.export(value, "研究報告", H, enabled=True)["readyForWriterReview"])

    def test_output_never_becomes_formal_evidence(self):
        output = exporter.export(manifest(), "研究報告", H, enabled=True)
        self.assertEqual(output["mode"], "research_only")
        self.assertFalse(output["promotionEligible"])
        self.assertIn("no_ledger_or_external_anchor", output["limitations"])

    def test_module_has_no_io_network_env_db_or_formal_flow(self):
        source = inspect.getsource(exporter).lower()
        for forbidden in ("open(", "pathlib", "requests", "urllib", "socket", "subprocess",
                          "psycopg", "\nimport supabase", "\nfrom supabase", "os.environ",
                          "getenv(", "telegram", "strategy_tracker", "promotion_status",
                          "investment_advice", "execute("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
