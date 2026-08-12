import copy
import json
import os
import unittest

import official_availability_capability as capability


def contract(source_key, **changes):
    provider, contract_id = capability.EXPECTED[source_key]
    value = {
        "schemaVersion": 1,
        "sourceKey": source_key,
        "provider": provider,
        "contractId": contract_id,
        "documentationEvidenceId": f"official-doc:{contract_id}:v1",
        "termsStatus": "open_data_attribution",
        "evidenceMode": "official_timestamp",
        "publicationSemantic": "official_published_timestamp",
        "timestampPrecision": "timestamp_with_timezone",
        "timezoneDocumented": True,
        "immutableIdentityDocumented": True,
        "revisionSemanticsDocumented": False,
        "historyCoverage": "complete",
        "usesUndocumentedHttpHeaders": False,
        "conflictStatus": "no_conflict",
        "contentHash": "a" * 64,
        "schemaHash": "b" * 64,
    }
    value.update(changes)
    return value


def complete():
    return [contract(source) for source in capability.EXPECTED]


class OfficialAvailabilityCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        os.environ[capability.FLAG] = "true"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    def test_all_accepted_is_still_research_only_and_emits_no_time(self):
        result = capability.audit(complete())
        self.assertEqual(result["certifiableCoverage"], 1.0)
        self.assertEqual(result["coverageRequirement"], 1.0)
        self.assertTrue(result["capabilityReady"])
        self.assertEqual(result["mode"], "research_only")
        rendered = json.dumps(result)
        for forbidden in ("availableAt", "publishedAt", "retrievedAt", "generatedAt"):
            self.assertNotIn(forbidden, rendered)

    def test_date_only_or_current_only_is_provisional(self):
        values = complete()
        values[0].update(
            timestampPrecision="date_only",
            timezoneDocumented=False,
            publicationSemantic="official_notice_date",
        )
        values[1]["historyCoverage"] = "current_only"
        result = capability.audit(values)
        self.assertEqual(result["records"][0]["classification"], "PROVISIONAL_DATE_ONLY")
        self.assertIn("timestamp_precision_insufficient", result["records"][0]["reasons"])
        self.assertEqual(result["records"][1]["classification"], "UNSUPPORTED")
        self.assertFalse(result["capabilityReady"])

    def test_observation_effective_generated_retrieved_and_quality_check_reject(self):
        for semantic in capability.REJECTED_SEMANTICS:
            values = complete()
            values[0]["publicationSemantic"] = semantic
            result = capability.audit(values)
            self.assertEqual(result["records"][0]["classification"], "UNSUPPORTED")
            self.assertIn("non_publication_time_semantic", result["records"][0]["reasons"])

    def test_undocumented_http_headers_are_never_evidence(self):
        values = complete()
        values[0]["usesUndocumentedHttpHeaders"] = True
        result = capability.audit(values)
        self.assertEqual(result["records"][0]["classification"], "UNSUPPORTED")
        self.assertIn("undocumented_http_header_forbidden", result["records"][0]["reasons"])

    def test_immutable_revision_requires_documented_semantics(self):
        values = complete()
        values[0]["evidenceMode"] = "immutable_revision"
        result = capability.audit(values)
        self.assertEqual(result["records"][0]["classification"], "UNSUPPORTED")
        values[0]["revisionSemanticsDocumented"] = True
        self.assertEqual(capability.audit(values)["records"][0]["classification"], "CERTIFIABLE")

    def test_missing_duplicate_scope_and_allowlist_fail_closed(self):
        result = capability.audit(complete()[:-1])
        self.assertEqual(result["counts"]["missing"], 1)
        self.assertFalse(result["capabilityReady"])
        values = complete() + [copy.deepcopy(complete()[0])]
        self.assertEqual(capability.audit(values)["records"][0]["classification"], "CONFLICT")
        values = complete() + [{"sourceKey": "other"}]
        result = capability.audit(values)
        self.assertEqual(result["blockers"], ["input_scope_invalid"])
        self.assertEqual(result["counts"]["certifiable"], 0)
        values = complete()
        values[0]["contractId"] = "wrong"
        self.assertEqual(capability.audit(values)["records"][0]["classification"], "UNSUPPORTED")

    def test_raw_url_secret_and_prohibited_terms_reject_without_leak(self):
        for key, value in (("endpoint", "https://example.test"), ("rawRows", [1]), ("note", "token=secret")):
            values = complete()
            values[0][key] = value
            result = capability.audit(values)
            self.assertEqual(result["records"][0]["classification"], "UNSUPPORTED")
            rendered = json.dumps(result)
            self.assertNotIn("example.test", rendered)
            self.assertNotIn("token=secret", rendered)
            self.assertNotIn("rawRows", rendered)
        values = complete()
        values[0]["termsStatus"] = "prohibited"
        self.assertEqual(capability.audit(values)["records"][0]["classification"], "UNSUPPORTED")

    def test_contradiction_is_conflict(self):
        values = complete()
        values[0]["conflictStatus"] = "conflict"
        result = capability.audit(values)
        self.assertEqual(result["records"][0]["classification"], "CONFLICT")
        self.assertEqual(result["counts"]["conflict"], 1)

    def test_default_off_is_side_effect_free_and_digest_is_deterministic(self):
        values = complete()
        before = copy.deepcopy(values)
        first = capability.audit(values)
        second = capability.audit(copy.deepcopy(values))
        self.assertEqual(first["capabilityDigest"], second["capabilityDigest"])
        os.environ.pop(capability.FLAG)
        self.assertEqual(capability.audit(values)["mode"], "disabled")
        self.assertEqual(values, before)


if __name__ == "__main__":
    unittest.main()
