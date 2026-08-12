import copy
import inspect
import json
import unittest

import staging_preflight_result as result_contract


def valid_package():
    return {
        "schemaVersion": 1,
        "mode": "research_only",
        "diagnosticOnly": True,
        "scope": result_contract.SCOPE,
        "environment": "staging",
        "mainSha": result_contract.MAIN_SHA,
        "authoritySourcePin": result_contract.AUTHORITY_SOURCE_PIN,
        "dryRunSourcePin": result_contract.DRY_RUN_SOURCE_PIN,
        "queryPin": result_contract.QUERY_PIN,
        "authorityContractDigest": "1" * 64,
        "stagingRefHash": "2" * 64,
        "productionRefHash": "3" * 64,
        "preflightSummary": copy.deepcopy(result_contract.SUMMARY_EXPECTED),
        "manualAttestations": copy.deepcopy(result_contract.ATTESTATION_EXPECTED),
        "retention": copy.deepcopy(result_contract.RETENTION_EXPECTED),
        "audit": {
            "authorityTicketHash": "4" * 64,
            "operatorAttestationHash": "5" * 64,
            "capturedAt": "2026-08-12T12:00:00Z",
        },
    }


class StagingPreflightResultTests(unittest.TestCase):
    def test_default_off_is_bounded_and_side_effect_free(self):
        output = result_contract.validate({})
        self.assertEqual(output["mode"], "disabled")
        self.assertFalse(output["readyForTransactionDryRun"])

    def test_valid_result_is_deterministic_research_only(self):
        first = result_contract.validate(valid_package(), enabled=True)
        second = result_contract.validate(dict(reversed(list(valid_package().items()))), enabled=True)
        self.assertEqual(first, second)
        self.assertTrue(first["readyForTransactionDryRun"])
        self.assertEqual(first["mode"], "research_only")
        self.assertLess(len(json.dumps(first)), 1600)

    def test_output_is_allowlisted_and_does_not_echo_input_hashes(self):
        package = valid_package()
        output = result_contract.validate(package, enabled=True)
        self.assertEqual(set(output), {
            "schemaVersion", "mode", "diagnosticOnly", "readyForTransactionDryRun",
            "packageDigest", "blockers", "retentionClass", "limitations",
        })
        rendered = json.dumps(output)
        for name in ("stagingRefHash", "productionRefHash", "authorityContractDigest"):
            self.assertNotIn(package[name], rendered)

    def test_exact_pins_and_project_separation_are_required(self):
        for name in ("mainSha", "authoritySourcePin", "dryRunSourcePin", "queryPin"):
            package = valid_package(); package[name] = "0" * len(package[name])
            self.assertIn("identity_or_pin_invalid", result_contract.validate(package, enabled=True)["blockers"])
        package = valid_package(); package["productionRefHash"] = package["stagingRefHash"]
        self.assertIn("project_separation_unverified", result_contract.validate(package, enabled=True)["blockers"])

    def test_every_preflight_assertion_is_fail_closed(self):
        for field, expected in result_contract.SUMMARY_EXPECTED.items():
            package = valid_package()
            package["preflightSummary"][field] = not expected if isinstance(expected, bool) else 1
            output = result_contract.validate(package, enabled=True)
            self.assertFalse(output["readyForTransactionDryRun"])
            self.assertIn("preflight_summary_failed", output["blockers"])

    def test_manual_attestations_and_retention_are_exact(self):
        for group, expected in (("manualAttestations", result_contract.ATTESTATION_EXPECTED),
                                ("retention", result_contract.RETENTION_EXPECTED)):
            for field, value in expected.items():
                package = valid_package()
                package[group][field] = not value if isinstance(value, bool) else value + 1
                self.assertFalse(result_contract.validate(package, enabled=True)["readyForTransactionDryRun"])

    def test_audit_contract_is_strict_and_timestamp_is_explicit_utc(self):
        for field, value in (("capturedAt", "2026-08-12"),
                             ("capturedAt", "2026-08-12T20:00:00+08:00"),
                             ("authorityTicketHash", "raw-ticket"),
                             ("operatorAttestationHash", None)):
            package = valid_package(); package["audit"][field] = value
            self.assertIn("audit_contract_invalid", result_contract.validate(package, enabled=True)["blockers"])

    def test_unknown_fields_and_sensitive_raw_values_are_rejected_without_echo(self):
        package = valid_package(); package["rawRows"] = []
        self.assertIn("package_contract_invalid", result_contract.validate(package, enabled=True)["blockers"])
        for value in ("https://example.invalid", "postgresql://u:p@h/db", "Bearer secret",
                      "select * from private_table;", "service_role"):
            package = valid_package(); package["scope"] = value
            output = result_contract.validate(package, enabled=True)
            self.assertIn("sensitive_raw_or_sql_value_forbidden", output["blockers"])
            self.assertNotIn(value, json.dumps(output))

    def test_module_has_no_io_network_db_env_clock_or_formal_flow(self):
        source = inspect.getsource(result_contract).lower()
        for forbidden in ("open(", "pathlib", "urllib", "requests", "socket", "subprocess",
                          "psycopg", "\nimport supabase", "\nfrom supabase", "os.environ",
                          "getenv(", "datetime", "time.", "\nimport candidate_manifest",
                          "\nfrom candidate_manifest", "\nimport backtest", "\nfrom backtest",
                          "\nimport telegram", "\nfrom telegram", "\nimport investment_advice",
                          "\nfrom investment_advice", "execute("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
