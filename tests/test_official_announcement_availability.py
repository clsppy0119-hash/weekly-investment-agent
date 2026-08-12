import copy
import hashlib
import inspect
import unittest

import official_announcement_availability as contract

KEY = "twse:listing:2330"


def base_record():
    return {
        "provider": "TWSE", "sourceKey": "twse_listed",
        "documentId": "official-letter-1", "revisionId": "rev-1",
        "evidenceKey": KEY, "entity": "2330", "eventType": "listing",
        "effectiveDate": "2020-01-02",
        "contentHash": hashlib.sha256(b"fixture").hexdigest(),
        "officialEvidence": True,
    }


def run(record, *, mode="historical", decision="2024-01-02T00:00:00+08:00"):
    return contract.validate([record], expected_keys=[KEY], decision_as_of=decision,
                             evaluation_mode=mode, enabled=True)


class OfficialAnnouncementAvailabilityTests(unittest.TestCase):
    def test_default_off_has_no_records(self):
        result = contract.validate([], expected_keys=[], decision_as_of="bad", evaluation_mode="bad")
        self.assertEqual(result["mode"], "disabled")
        self.assertEqual(result["recordCount"], 0)

    def test_individual_official_timezone_timestamp_is_historical(self):
        record = base_record()
        record.update({"publishedAt": "2024-01-01T09:00:00+08:00",
                       "publishedAtBindsExactRevision": True,
                       "publicationEvidenceId": "official-publish-field-v1"})
        result = run(record)
        self.assertTrue(result["contractReady"])
        self.assertEqual(result["records"][0]["classification"], contract.HISTORICAL_CERTIFIABLE)

    def test_immutable_revision_documented_semantics_is_historical(self):
        record = base_record()
        record.update({"revisionAvailableAt": "2024-01-01T09:00:00+08:00",
                       "immutableRevision": True, "publicationSemanticsDocumented": True,
                       "revisionAvailableAtBindsExactRevision": True,
                       "publicationSemanticsId": "official-revision-contract-v1"})
        self.assertTrue(run(record)["contractReady"])

    def test_date_only_bare_9am_and_headers_are_rejected(self):
        for field in ("dateOnlyEvidence", "bareDailySchedule", "usesHttpDate", "usesLastModified"):
            with self.subTest(field=field):
                record = base_record()
                record.update({field: True, "firstSeenAt": "2024-01-01T09:00:00+08:00",
                               "firstSeenAppendOnly": True})
                result = run(record, mode="forward")
                self.assertFalse(result["contractReady"])
                self.assertEqual(result["records"][0]["classification"], contract.REJECTED)

    def test_timezone_less_timestamp_is_rejected(self):
        record = base_record()
        record.update({"publishedAt": "2024-01-01T09:00:00",
                       "publishedAtBindsExactRevision": True,
                       "publicationEvidenceId": "official-publish-field-v1"})
        self.assertFalse(run(record)["contractReady"])

    def test_first_seen_is_forward_only_after_observation(self):
        record = base_record()
        record.update({"firstSeenAt": "2024-01-01T09:00:00+08:00", "firstSeenAppendOnly": True})
        forward = run(record, mode="forward")
        self.assertTrue(forward["contractReady"])
        self.assertEqual(forward["records"][0]["classification"], contract.FORWARD_OBSERVED_ONLY)
        historical = run(record, mode="historical")
        self.assertFalse(historical["contractReady"])
        self.assertEqual(historical["records"][0]["reason"], "first_seen_forbidden_for_historical_backfill")

    def test_first_seen_never_selects_before_observation(self):
        record = base_record()
        record.update({"firstSeenAt": "2024-01-03T09:00:00+08:00", "firstSeenAppendOnly": True})
        result = run(record, mode="forward")
        self.assertFalse(result["contractReady"])
        self.assertEqual(result["records"][0]["reason"], "not_available_at_decision")

    def test_duplicate_is_noop_but_changed_first_seen_conflicts(self):
        record = base_record()
        record.update({"firstSeenAt": "2024-01-01T09:00:00+08:00", "firstSeenAppendOnly": True})
        duplicate = contract.validate([record, copy.deepcopy(record)], expected_keys=[KEY],
                                      decision_as_of="2024-01-02T00:00:00+08:00",
                                      evaluation_mode="forward", enabled=True)
        self.assertTrue(duplicate["contractReady"])
        changed = copy.deepcopy(record)
        changed["firstSeenAt"] = "2024-01-01T10:00:00+08:00"
        conflict = contract.validate([record, changed], expected_keys=[KEY],
                                     decision_as_of="2024-01-02T00:00:00+08:00",
                                     evaluation_mode="forward", enabled=True)
        self.assertFalse(conflict["contractReady"])
        self.assertEqual(conflict["records"][0]["classification"], contract.CONFLICT)

    def test_coverage_must_be_100_percent(self):
        record = base_record()
        record.update({"publishedAt": "2024-01-01T09:00:00+08:00",
                       "publishedAtBindsExactRevision": True,
                       "publicationEvidenceId": "official-publish-field-v1"})
        result = contract.validate([record], expected_keys=[KEY, "tpex:listing:6488"],
                                   decision_as_of="2024-01-02T00:00:00+08:00",
                                   evaluation_mode="historical", enabled=True)
        self.assertFalse(result["contractReady"])
        self.assertEqual(result["coverage"], 0.5)

    def test_multiple_eligible_revisions_are_ambiguous_and_fail_closed(self):
        first = base_record()
        first.update({"publishedAt": "2024-01-01T09:00:00+08:00",
                      "publishedAtBindsExactRevision": True,
                      "publicationEvidenceId": "official-publish-field-v1"})
        second = copy.deepcopy(first)
        second.update({"revisionId": "rev-2", "contentHash": hashlib.sha256(b"revision-2").hexdigest()})
        result = contract.validate([first, second], expected_keys=[KEY],
                                   decision_as_of="2024-01-02T00:00:00+08:00",
                                   evaluation_mode="historical", enabled=True)
        self.assertFalse(result["contractReady"])
        self.assertEqual(result["selectedCount"], 0)
        self.assertEqual(result["conflictCount"], 1)
        self.assertTrue(all(item["classification"] == contract.CONFLICT for item in result["records"]))

    def test_sensitive_or_raw_fields_fail_closed(self):
        for field in ("url", "token", "rawRows", "retrievedAt", "generatedAt"):
            record = base_record()
            record[field] = "forbidden"
            self.assertEqual(run(record)["records"][0]["reason"], "forbidden_or_sensitive_field")

    def test_hash_is_deterministic_for_exact_input(self):
        record = base_record()
        record.update({"publishedAt": "2024-01-01T09:00:00+08:00",
                       "publishedAtBindsExactRevision": True,
                       "publicationEvidenceId": "official-publish-field-v1"})
        self.assertEqual(run(record)["reportHash"], run(copy.deepcopy(record))["reportHash"])

    def test_no_network_database_secret_or_formal_imports(self):
        source = inspect.getsource(contract)
        for forbidden in ("urllib", "requests", "socket", "supabase", "dotenv", "os.environ",
                          "official_metadata_smoke", "pit_availability_evidence"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
