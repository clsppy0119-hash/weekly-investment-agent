import copy
import json
import os
import unittest

import pit_availability_evidence as evidence


def row(source_key, version="v1", *, published="2026-08-10T09:00:00+08:00", parent=None):
    provider, dataset = evidence.OFFICIAL_SOURCES[source_key]
    value = {
        "schemaVersion": 1,
        "versionId": version,
        "sourceKey": source_key,
        "provider": provider,
        "dataset": dataset,
        "evidenceKind": "official_publication_timestamp",
        "authorityField": "officialPublishedAt",
        "officialPublishedAt": published,
        "availableAt": published,
        "effectiveDate": "2026-08-10",
        "contentHash": "a" * 64,
        "schemaHash": "b" * 64,
        "conflictStatus": "no_conflict",
        "visibility": "private_metadata",
    }
    if parent:
        value["supersedesVersionId"] = parent
    return value


def complete():
    return [row(source) for source in evidence.OFFICIAL_SOURCES]


class OfficialAvailabilityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        os.environ[evidence.FLAG] = "true"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    def run_report(self, records=None, decision="2026-08-12T08:00:00+08:00"):
        return evidence.validate(complete() if records is None else records, decision_as_of=decision)

    def test_explicit_official_timestamp_selects_but_never_promotes(self):
        result = self.run_report()
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["counts"]["selectedVersion"], 4)
        self.assertEqual(result["mode"], "research_only")
        self.assertTrue(result["diagnosticOnly"])
        self.assertIn("does not prove normalized values", result["limitation"])

    def test_observation_generation_or_retrieval_time_never_becomes_available_at(self):
        for time_key in ("observationDate", "generatedAt", "retrievedAt"):
            records = complete()
            item = records[0]
            item.pop("officialPublishedAt")
            item.pop("availableAt")
            item["evidenceKind"] = "official_report_date"
            item["authorityField"] = time_key
            item[time_key] = "2026-08-10T09:00:00+08:00"
            result = self.run_report(records)
            first = result["records"][0]
            self.assertEqual(first["availableAtState"], "unknown")
            self.assertEqual(first["selection"], "selectedNone")
            self.assertIn("official_availability_evidence_missing", first["reasons"])

    def test_append_only_revision_preserves_old_and_selects_terminal(self):
        records = complete()
        old = records[0]
        new = row("twse_listed", "v2", published="2026-08-11T09:00:00+08:00", parent="v1")
        new["contentHash"] = "c" * 64
        records.append(new)
        result = self.run_report(records)
        first = result["records"][0]
        self.assertEqual(first["versionCount"], 2)
        self.assertEqual(first["appendOnlyState"], "valid")
        self.assertEqual(first["selectedVersionId"], "v2")

    def test_identical_retry_is_noop_but_overwrite_and_cycle_fail(self):
        records = complete()
        records.append(copy.deepcopy(records[0]))
        result = self.run_report(records)
        self.assertEqual(result["counts"]["duplicateNoOp"], 1)
        self.assertEqual(result["records"][0]["selection"], "selectedVersion")

        overwritten = complete()
        changed = copy.deepcopy(overwritten[0])
        changed["contentHash"] = "c" * 64
        overwritten.append(changed)
        first = self.run_report(overwritten)["records"][0]
        self.assertIn("version_overwrite_detected", first["reasons"])
        self.assertEqual(first["selection"], "selectedNone")

        cyclic = complete()
        cyclic[0]["supersedesVersionId"] = "v2"
        cyclic.append(row("twse_listed", "v2", parent="v1"))
        first = self.run_report(cyclic)["records"][0]
        self.assertIn("append_only_chain_cycle", first["reasons"])

    def test_future_conflict_partial_and_timezone_fail_closed(self):
        future = complete()
        future[0]["officialPublishedAt"] = future[0]["availableAt"] = "2026-09-01T09:00:00+08:00"
        self.assertEqual(self.run_report(future)["records"][0]["selection"], "selectedNone")
        conflict = complete()
        conflict[0]["conflictStatus"] = "conflict"
        self.assertEqual(self.run_report(conflict)["counts"]["conflict"], 1)
        self.assertEqual(self.run_report(complete()[:-1])["counts"]["missing"], 1)
        invalid = self.run_report(decision="2026-08-12T08:00:00")
        self.assertEqual(invalid["blockers"], ["decision_as_of_invalid"])

    def test_strict_allowlist_and_secret_url_scan(self):
        for key, value in (("rawRows", [{"x": 1}]), ("endpoint", "https://example.test"), ("note", "token=secret")):
            records = complete()
            records[0][key] = value
            result = self.run_report(records)
            self.assertIn("metadata_not_allowlisted", result["records"][0]["reasons"])
            rendered = json.dumps(result)
            self.assertNotIn("example.test", rendered)
            self.assertNotIn("token=secret", rendered)
            self.assertNotIn("rawRows", rendered)

        records = complete() + [{"sourceKey": None}]
        result = self.run_report(records)
        self.assertEqual(result["blockers"], ["out_of_scope_source"])
        self.assertEqual(result["counts"]["selectedVersion"], 0)

    def test_default_off_has_no_validation_or_side_effect(self):
        os.environ.pop(evidence.FLAG)
        original = complete()
        before = copy.deepcopy(original)
        result = evidence.validate(original, decision_as_of="not-a-time")
        self.assertEqual(result["mode"], "disabled")
        self.assertEqual(original, before)


if __name__ == "__main__":
    unittest.main()
