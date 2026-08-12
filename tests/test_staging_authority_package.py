import copy
import inspect
import json
import unittest

import staging_authority_package as authority


def valid_package():
    return {
        "schemaVersion": 1, "mode": "research_only", "diagnosticOnly": True,
        "scope": authority.SCOPE, "environment": "staging", "mainSha": authority.MAIN_SHA,
        "b2a2SqlPin": authority.B2A2_SQL_PIN,
        "b2a2SemanticPin": authority.B2A2_SEMANTIC_PIN,
        "b2b1PreflightPin": authority.B2B1_PREFLIGHT_PIN,
        "b2b1ValidatorPin": authority.B2B1_VALIDATOR_PIN,
        "stagingRefHash": "1" * 64, "productionRefHash": "2" * 64,
        "authorityTicketHash": "3" * 64,
        "sqlSummary": {
            "schemaVersion": 1, "executorRoleIsPostgres": True,
            "transactionReadOnly": True, "pgcryptoNamespaceExact": True,
            "digestSignaturePresent": True, "targetRoleCount": 0,
            "targetSchemaCount": 0, "targetRelationCount": 0,
            "targetRoutineCount": 0, "authenticatorOverrideKnown": False,
            "privateSchemaInAuthenticatorOverride": False,
            "privateRuntimeGrantCount": 0, "privateViewExposureCount": 0,
            "privateRoutineExposureCount": 0, "privatePublicationExposureCount": 0,
        },
        "manualAttestations": copy.deepcopy(authority.MANUAL_EXPECTED),
        "noWriteAttestations": copy.deepcopy(authority.NO_WRITE_EXPECTED),
    }


class StagingAuthorityPackageTests(unittest.TestCase):
    def test_default_off_is_bounded_and_side_effect_free(self):
        result = authority.validate({})
        self.assertEqual(result["mode"], "disabled")
        self.assertFalse(result["ready"])

    def test_valid_package_is_research_only_and_deterministic(self):
        first = authority.validate(valid_package(), enabled=True)
        reordered = dict(reversed(list(valid_package().items())))
        second = authority.validate(reordered, enabled=True)
        self.assertTrue(first["ready"], first["blockers"])
        self.assertEqual(first, second)
        self.assertEqual(first["mode"], "research_only")
        self.assertLess(len(json.dumps(first)), 2048)

    def test_output_is_exact_and_never_echoes_input_hashes(self):
        package = valid_package()
        result = authority.validate(package, enabled=True)
        self.assertEqual(set(result), {"schemaVersion", "mode", "diagnosticOnly", "ready",
                                       "contractDigest", "blockers", "limitations"})
        rendered = json.dumps(result)
        for value in (package["stagingRefHash"], package["productionRefHash"],
                      package["authorityTicketHash"]):
            self.assertNotIn(value, rendered)

    def test_every_pin_and_identity_is_exact(self):
        fields = ("mainSha", "b2a2SqlPin", "b2a2SemanticPin",
                  "b2b1PreflightPin", "b2b1ValidatorPin")
        for field in fields:
            package = valid_package(); package[field] = "0" * len(package[field])
            result = authority.validate(package, enabled=True)
            self.assertFalse(result["ready"])
            self.assertIn("identity_or_pin_invalid", result["blockers"])

    def test_ref_hashes_must_be_valid_and_distinct(self):
        for staging, production in (("1" * 64, "1" * 64), ("raw-ref", "2" * 64),
                                    ("1" * 64, None)):
            package = valid_package(); package["stagingRefHash"] = staging
            package["productionRefHash"] = production
            self.assertIn("project_separation_unverified",
                          authority.validate(package, enabled=True)["blockers"])

    def test_missing_extra_and_nested_sensitive_values_fail_without_echo(self):
        package = valid_package(); package["rawProjectRef"] = "project-raw"
        self.assertIn("package_contract_invalid", authority.validate(package, enabled=True)["blockers"])
        for value in ("https://example.invalid", "postgresql://user:pw@host/db",
                      "Bearer abc", "select * from private_table;", "service_role"):
            package = valid_package(); package["scope"] = value
            result = authority.validate(package, enabled=True)
            self.assertIn("sensitive_or_raw_value_forbidden", result["blockers"])
            self.assertNotIn(value, json.dumps(result))

    def test_sql_summary_fails_closed_for_each_unsafe_class(self):
        mutations = (
            ("executorRoleIsPostgres", False), ("transactionReadOnly", False),
            ("pgcryptoNamespaceExact", False), ("digestSignaturePresent", False),
            ("targetRoleCount", 1), ("targetSchemaCount", -1),
            ("targetRelationCount", "0"), ("targetRoutineCount", True),
            ("privateSchemaInAuthenticatorOverride", True),
            ("privateRuntimeGrantCount", 1), ("privateViewExposureCount", 1),
            ("privateRoutineExposureCount", 1), ("privatePublicationExposureCount", 1),
        )
        for field, value in mutations:
            package = valid_package(); package["sqlSummary"][field] = value
            self.assertIn("sql_summary_invalid",
                          authority.validate(package, enabled=True)["blockers"])

    def test_manual_and_no_write_attestations_cannot_be_overridden(self):
        for group, field in (("manualAttestations", "dashboardExposureVerified"),
                             ("manualAttestations", "privateSchemaInDashboardExposed"),
                             ("noWriteAttestations", "migrationExecuted"),
                             ("noWriteAttestations", "applicationRowsRead")):
            package = valid_package(); package[group][field] = not package[group][field]
            result = authority.validate(package, enabled=True)
            self.assertFalse(result["ready"])
        package = valid_package()
        package["sqlSummary"]["privateSchemaInAuthenticatorOverride"] = True
        self.assertFalse(authority.validate(package, enabled=True)["ready"])

    def test_any_missing_or_unknown_nested_field_fails(self):
        for group in ("sqlSummary", "manualAttestations", "noWriteAttestations"):
            package = valid_package(); package[group].pop(next(iter(package[group])))
            self.assertFalse(authority.validate(package, enabled=True)["ready"])
            package = valid_package(); package[group]["unknown"] = True
            self.assertFalse(authority.validate(package, enabled=True)["ready"])

    def test_source_has_no_io_network_db_env_clock_or_formal_imports(self):
        source = inspect.getsource(authority).lower()
        for forbidden in ("open(", "pathlib", "urllib", "requests", "socket", "subprocess",
                          "psycopg", "supabase", "dotenv", "os.environ", "getenv(",
                          "datetime", "time.", "candidate_manifest", "backtest", "telegram",
                          "investment_advice", "execute("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
