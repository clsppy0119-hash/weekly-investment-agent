import copy
import json
import os
import unittest

import contract_gap_report as gaps
import freeze_lineage_summary as freeze


def fixture():
    records = [
        {
            "name": name,
            "source": "official",
            "sourceDataset": "dataset",
            "effectiveDate": "2026-08-01",
            "availableAt": "2026-08-10T00:00:00+08:00",
            "evidenceHash": name,
            "quality": "verified",
            "conflictStatus": "no_conflict",
        }
        for name in gaps.EXPECTED
    ]
    contract = {"schemaVersion": 1, "certified": True, "records": records}
    contract["contractHash"] = gaps._hash(records)
    manifest = {
        "schemaVersion": 1,
        "candidateOrder": ["2330"],
        "dataContract": {"contractHash": contract["contractHash"]},
    }
    frozen = freeze.freeze(contract, manifest)
    return contract, frozen, manifest


class ContractGapReportTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        os.environ[gaps.FLAG] = "true"
        os.environ[freeze.FLAG] = "true"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    def _report(self, contract=None, frozen=None, manifest=None):
        if contract is None:
            contract, frozen, manifest = fixture()
        return gaps.report(contract, frozen, manifest, decision_as_of="2026-08-12T08:00:00+08:00")

    def test_complete_metadata_is_still_research_only(self):
        result = self._report()
        self.assertEqual(result["mode"], "research_only")
        self.assertTrue(result["diagnosticOnly"])
        self.assertEqual(result["coverageDenominator"], 4)
        self.assertEqual(result["counts"]["selectedVersion"], 4)
        self.assertEqual(result["coverage"]["pitSelected"], 1.0)
        self.assertEqual(result["coverageState"], "complete")

    def test_missing_unknown_conflict_invalid_and_stale_are_separate(self):
        contract, _, manifest = fixture()
        contract["records"] = [item for item in contract["records"] if item["name"] != "quote"]
        by = {item["name"]: item for item in contract["records"]}
        by["fundamentals"]["quality"] = "unknown"
        by["corporate_actions"]["conflictStatus"] = "provider_conflict"
        by["point_in_time"]["availableAt"] = "not-a-time"
        contract["certified"] = False
        contract["contractHash"] = gaps._hash(contract["records"])
        manifest["dataContract"]["contractHash"] = contract["contractHash"]
        frozen = freeze.freeze(contract, manifest)
        result = self._report(contract, frozen, manifest)
        self.assertEqual(result["counts"]["missing"], 1)
        self.assertEqual(result["counts"]["unknown"], 1)
        self.assertEqual(result["counts"]["conflict"], 1)
        self.assertEqual(result["counts"]["invalid"], 1)
        self.assertEqual(result["counts"]["selectedNone"], 4)

        stale_contract, _, stale_manifest = fixture()
        stale_contract["records"][0]["availableAt"] = "2026-01-01T00:00:00+08:00"
        stale_contract["contractHash"] = gaps._hash(stale_contract["records"])
        stale_manifest["dataContract"]["contractHash"] = stale_contract["contractHash"]
        stale_frozen = freeze.freeze(stale_contract, stale_manifest)
        self.assertEqual(self._report(stale_contract, stale_frozen, stale_manifest)["counts"]["stale"], 1)

    def test_ambiguous_future_and_hash_mismatch_select_none(self):
        contract, frozen, manifest = fixture()
        contract["records"].append(copy.deepcopy(contract["records"][0]))
        contract["records"][1]["availableAt"] = "2027-01-01T00:00:00+08:00"
        result = self._report(contract, frozen, manifest)
        self.assertEqual(result["hashStates"]["contractHash"], "mismatch")
        self.assertEqual(result["counts"]["selectedVersion"], 0)
        self.assertIn("version_ambiguous", result["records"][0]["reasons"])
        self.assertIn("available_at_after_decision", result["records"][1]["reasons"])

    def test_out_of_scope_is_counted_but_not_selected(self):
        contract, _, manifest = fixture()
        contract["records"].append({"name": "market_news", "quality": "context_only"})
        contract["contractHash"] = gaps._hash(contract["records"])
        manifest["dataContract"]["contractHash"] = contract["contractHash"]
        frozen = freeze.freeze(contract, manifest)
        result = self._report(contract, frozen, manifest)
        self.assertEqual(result["counts"]["outOfScope"], 1)
        self.assertEqual(result["counts"]["selectedVersion"], 4)

    def test_invalid_timezone_and_default_off_fail_closed(self):
        contract, frozen, manifest = fixture()
        result = gaps.report(contract, frozen, manifest, decision_as_of="2026-08-12T08:00:00")
        self.assertEqual(result["blockers"], ["decision_as_of_invalid"])
        os.environ.pop(gaps.FLAG)
        self.assertEqual(gaps.report(contract, frozen, manifest, decision_as_of="x")["mode"], "disabled")

    def test_output_is_allowlisted_metadata_only(self):
        rendered = json.dumps(self._report())
        for forbidden in ("https://", "endpoint", "payload", "rawRows", "retrievedAt", "ingestedAt"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
