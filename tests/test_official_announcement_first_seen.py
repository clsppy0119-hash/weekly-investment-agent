import copy
import hashlib
import inspect
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import official_announcement_first_seen as observer


def fixture(provider="TWSE", market="listed"):
    contract_id = (
        "twse_official_announcement_detail_v1"
        if provider == "TWSE" else "tpex_market_announcement_detail_v1"
    )
    return {
        "provider": provider, "market": market, "sourceContractId": contract_id,
        "officialDocumentId": "doc-001", "officialLetterNo": "letter-001",
        "entityId": "2330", "eventType": "listing", "effectiveDate": "2026-08-13",
        "sourceRevision": "rev-1", "contentHash": hashlib.sha256(b"fixture").hexdigest(),
        "officialEvidence": True,
    }


NOW = datetime(2026, 8, 12, 9, 30, 0, tzinfo=timezone.utc)


def observe(value, *, now=NOW):
    with mock.patch.object(observer, "_utc_now", return_value=now) as clock:
        result = observer.observe(value, enabled=True)
    return result, clock


class OfficialAnnouncementFirstSeenTests(unittest.TestCase):
    def test_default_off_does_not_read_clock(self):
        with mock.patch.object(observer, "_utc_now") as clock:
            result = observer.observe(fixture())
        self.assertEqual(result["mode"], "disabled")
        clock.assert_not_called()

    def test_valid_fixture_uses_internal_completion_utc_once(self):
        result, clock = observe(fixture())
        clock.assert_called_once_with()
        record = result["record"]
        self.assertEqual(record["firstSeenAtUtc"], "2026-08-12T09:30:00Z")
        self.assertEqual(record["classification"], observer.CLASSIFICATION)
        self.assertFalse(record["historicalEligible"])
        self.assertEqual(record["mode"], "research_only")
        self.assertNotIn("availableAt", record)
        self.assertNotIn("publishedAt", record)

    def test_caller_time_fields_are_rejected_before_clock(self):
        for field in ("firstSeenAt", "firstSeenAtUtc", "observedAt", "observationCompletedAt",
                      "availableAt", "publishedAt", "retrievedAt", "generatedAt"):
            with self.subTest(field=field):
                value = fixture()
                value[field] = "2020-01-01T00:00:00Z"
                result, clock = observe(value)
                self.assertIsNone(result["record"])
                self.assertIn("forbidden_or_caller_time_field", result["blockers"])
                clock.assert_not_called()

    def test_exact_source_contract_provider_market_and_membership_event(self):
        values = []
        wrong_contract = fixture()
        wrong_contract["sourceContractId"] = "unknown"
        values.append(wrong_contract)
        wrong_provider = fixture()
        wrong_provider["provider"] = "TPEX"
        values.append(wrong_provider)
        wrong_market = fixture()
        wrong_market["market"] = "otc"
        values.append(wrong_market)
        wrong_event = fixture()
        wrong_event["eventType"] = "dividend"
        values.append(wrong_event)
        for value in values:
            result, clock = observe(value)
            self.assertIsNone(result["record"])
            clock.assert_not_called()
        tpex, _ = observe(fixture("TPEX", "emerging"))
        self.assertIsNotNone(tpex["record"])

    def test_non_utc_observer_clock_fails_closed(self):
        naive, _ = observe(fixture(), now=datetime(2026, 8, 12, 9, 30))
        self.assertIn("observer_clock_not_utc", naive["blockers"])
        non_utc, _ = observe(fixture(), now=NOW.astimezone(timezone(timedelta(hours=8))))
        self.assertIn("observer_clock_not_utc", non_utc["blockers"])

    def test_ledger_duplicate_noop_and_changed_identity_conflict(self):
        record = observe(fixture())[0]["record"]
        ledger = observer.TemporaryAppendOnlyLedger()
        self.assertEqual(ledger.append(record)["status"], "appended")
        self.assertEqual(ledger.append(copy.deepcopy(record))["status"], "duplicate_noop")
        changed = copy.deepcopy(record)
        changed["firstSeenAtUtc"] = "2026-08-12T09:31:00Z"
        changed_payload = copy.deepcopy(changed)
        changed_payload.pop("recordHash")
        changed["recordHash"] = observer._canonical_hash(changed_payload)
        self.assertEqual(ledger.append(changed)["status"], "conflict")
        self.assertEqual(ledger.summary()["recordCount"], 1)

    def test_correction_requires_new_revision_and_exact_supersedes(self):
        original = observe(fixture())[0]["record"]
        ledger = observer.TemporaryAppendOnlyLedger()
        self.assertEqual(ledger.append(original)["status"], "appended")
        revised_fixture = fixture()
        revised_fixture.update({"sourceRevision": "rev-2",
                                "contentHash": hashlib.sha256(b"revision-2").hexdigest()})
        missing = observe(revised_fixture, now=NOW + timedelta(minutes=1))[0]["record"]
        self.assertEqual(ledger.append(missing)["reason"], "correction_missing_exact_supersedes")
        revised_fixture["supersedesContentHash"] = original["contentHash"]
        revised = observe(revised_fixture, now=NOW + timedelta(minutes=1))[0]["record"]
        self.assertEqual(ledger.append(revised)["status"], "appended")
        self.assertEqual(ledger.summary()["recordCount"], 2)

    def test_unknown_supersedes_is_rejected(self):
        value = fixture()
        value["supersedesContentHash"] = hashlib.sha256(b"unknown").hexdigest()
        record = observe(value)[0]["record"]
        ledger = observer.TemporaryAppendOnlyLedger()
        self.assertEqual(ledger.append(record)["reason"], "supersedes_unknown_version")

    def test_ledger_rejects_available_or_historical_record(self):
        record = observe(fixture())[0]["record"]
        for change in ({"availableAt": NOW.isoformat()}, {"historicalEligible": True},
                       {"classification": "HISTORICAL_CERTIFIABLE"}):
            changed = copy.deepcopy(record)
            changed.update(change)
            self.assertEqual(observer.TemporaryAppendOnlyLedger().append(changed)["status"], "blocked")

    def test_ledger_recomputes_record_and_composite_hashes(self):
        record = observe(fixture())[0]["record"]
        changed_content = copy.deepcopy(record)
        changed_content["officialLetterNo"] = "letter-forged"
        self.assertEqual(observer.TemporaryAppendOnlyLedger().append(changed_content)["status"], "blocked")
        changed_key = copy.deepcopy(record)
        changed_key["compositeKey"] = hashlib.sha256(b"forged-key").hexdigest()
        payload = copy.deepcopy(changed_key)
        payload.pop("recordHash")
        changed_key["recordHash"] = observer._canonical_hash(payload)
        self.assertEqual(observer.TemporaryAppendOnlyLedger().append(changed_key)["status"], "blocked")

    def test_schema_mapping_mismatch_is_explicit_and_never_shimmed(self):
        disabled = observer.validate_schema_mapping()
        self.assertFalse(disabled["mappingReady"])
        result = observer.validate_schema_mapping(enabled=True)
        self.assertEqual(result["status"], "blocked_contract_mismatch")
        self.assertFalse(result["mappingReady"])
        self.assertIn("metadata", result["missingInWriter"])
        self.assertIn("entity_id", result["missingInWriter"])
        self.assertIn("entityId", result["unexpectedWriter"])
        self.assertIn("required_metadata_not_written", result["blockers"])
        self.assertEqual(result["limitations"], ["no_shim", "no_migration", "no_writer_call"])

    def test_sanitized_ledger_summary_contains_no_record_details(self):
        record = observe(fixture())[0]["record"]
        ledger = observer.TemporaryAppendOnlyLedger()
        ledger.append(record)
        summary = ledger.summary()
        rendered = str(summary)
        for forbidden in ("2330", "doc-001", "letter-001", "contentHash", "firstSeenAtUtc"):
            self.assertNotIn(forbidden, rendered)

    def test_hashes_are_deterministic_for_same_internal_clock(self):
        first = observe(fixture())[0]["record"]
        second = observe(copy.deepcopy(fixture()))[0]["record"]
        self.assertEqual(first["compositeKey"], second["compositeKey"])
        self.assertEqual(first["recordHash"], second["recordHash"])

    def test_module_has_no_io_database_secret_or_formal_imports(self):
        source = inspect.getsource(observer)
        for forbidden in ("urllib", "requests", "socket", "subprocess", "supabase", "dotenv",
                          "os.environ", "lineage_writer", "pit_availability_evidence",
                          "candidate_manifest", "backtest", "telegram"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
