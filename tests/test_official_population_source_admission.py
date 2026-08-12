import ast
import copy
import json
from pathlib import Path
import unittest

import official_full_market_population as population_producer
import official_population_source_admission as admission


ROOT = Path(__file__).resolve().parents[1]


def hx(seed):
    return admission._digest({"seed": seed})


def evidence(source_slot, mode="historical"):
    pin = admission.SOURCE_SLOT_PINS[source_slot]
    value = {
        "sourceSlot": source_slot,
        "componentId": pin["componentId"],
        "providerAlias": pin["providerAlias"],
        "sourceContractId": pin["sourceContractId"],
        "datasetId": pin["datasetId"],
        "endpointContractHash": pin["endpointContractHash"],
        "providerLegalIdentityHash": hx(source_slot + "-provider"),
        "schemaHash": hx(source_slot + "-schema"),
        "documentationEvidenceId": source_slot + "-official-doc-v1",
        "documentationEvidenceHash": hx(source_slot + "-doc"),
        "obligationIds": list(pin["obligationIds"]),
        "coverageClass": "complete_history",
        "effectiveSemanticsClass": pin["requiredEffectiveSemantics"],
        "availabilityEvidenceClass": "official_timezone_timestamp",
        "revisionPublishedAt": "2000-01-01T09:00:00+08:00",
        "observationCompletedAt": None,
        "publicationSemanticsHash": hx(source_slot + "-publication"),
        "revisionIdentityScheme": "official-immutable-version",
        "immutableRevision": True,
        "appendOnly": True,
        "correctionPolicy": "append-only-supersedes",
        "conflictStatus": "none",
        "historyStart": "2000-01-01",
        "historyEnd": "2025-12-31",
        "expectedPages": 1,
        "parsedPages": 1,
        "expectedRecords": 10,
        "parsedRecords": 10,
        "rejectedRecords": 0,
        "termsVersion": "official-open-data-terms-v1",
        "termsHash": hx(source_slot + "-terms"),
        "termsStatus": "allowed",
        "attributionVersion": "official-attribution-v1",
        "attributionHash": hx(source_slot + "-attribution"),
        "attributionStatus": "satisfied",
        "legalPermissions": list(admission.LEGAL_PERMISSIONS),
        "evidenceContentHash": hx(source_slot + "-content"),
    }
    if mode == "forward":
        value.update({
            "coverageClass": "forward_only",
            "availabilityEvidenceClass": "observer_first_seen",
            "revisionPublishedAt": None,
            "observationCompletedAt": "2025-01-02T16:00:00+08:00",
            "publicationSemanticsHash": hx(source_slot + "-forward-boundary"),
            "revisionIdentityScheme": "current-snapshot-no-history",
            "immutableRevision": False,
            "correctionPolicy": "append-only-observations",
            "historyStart": None,
            "historyEnd": None,
        })
    return value


def payload(modes=None):
    modes = modes or {}
    sources = [evidence(slot, modes.get(slot, "historical")) for slot in admission.SOURCE_SLOTS]
    sources.sort(key=lambda row: row["sourceSlot"])
    return {
        "schemaVersion": admission.SCHEMA_VERSION,
        "policyVersion": admission.POLICY_VERSION,
        "termsPolicyVersion": admission.TERMS_POLICY_VERSION,
        "timezone": admission.TIMEZONE,
        "populationPolicyHash": admission.PINNED_POPULATION_POLICY_HASH,
        "node50ContractHash": admission.PINNED_NODE50_CONTRACT_HASH,
        "node51ReceiptPolicyHash": admission.PINNED_NODE51_RECEIPT_POLICY_HASH,
        "obligationMatrixHash": admission.OBLIGATION_MATRIX_HASH,
        "studyFrom": "2001-01-01",
        "studyTo": "2025-12-31",
        "sourceEvidence": sources,
        "evidenceSetHash": admission._digest(sources),
    }


def rehash(value):
    value["sourceEvidence"].sort(key=lambda row: row.get("sourceSlot", ""))
    value["evidenceSetHash"] = admission._digest(value["sourceEvidence"])


def test_self_declared_complete_history_cannot_exceed_fixed_slot_capabilities():
    result = admission.run(payload(), enabled=True)
    assert result["contractStructurallyValid"] is True
    assert result["historicalEvidenceShapeComplete"] is False
    assert result["historicalCandidateCount"] == 0
    assert result["rejectedUnknownCount"] == len(admission.SOURCE_SLOTS) == 6
    assert result["obligationCount"] == len(admission.OBLIGATIONS)
    by_slot = {row["sourceSlot"]: row for row in result["sourceResults"]}
    for slot in ("twse_current_master", "tpex_current_master", "tpex_emerging_current_master"):
        assert "current_master_not_historical_capable" in by_slot[slot]["reasonCodes"]
    assert "supplemental_exit_source_not_independently_admissible" in \
        by_slot["twse_terminated_master"]["reasonCodes"]
    for slot in ("twse_membership_events", "tpex_membership_events"):
        assert "source_contract_unregistered" in by_slot[slot]["reasonCodes"]
    for key in (
        "officialIdentityReady", "authoritativeAvailabilityReady",
        "node50AdmissionEligible", "sourceAllowlistEligible", "sourceAdmitted",
        "historicalEligible", "forwardEligible", "officialProducerRegistered",
        "pitCoverageCertified", "strategyValidated", "promotionEligible",
        "adviceEnabled", "trustedReceipt", "registryEligible", "formalGateAttached",
    ):
        assert result[key] is False
    assert result["registryEntryCount"] == 0
    assert set(admission.FIXED_BLOCKERS).issubset(result["blockers"])


def test_default_off_never_inspects_payload():
    class Explodes:
        def __getattribute__(self, name):
            raise AssertionError(name)

    assert admission.run(Explodes(), enabled=False) == {
        "schemaVersion": 1,
        "policyVersion": admission.POLICY_VERSION,
        "mode": "disabled",
        "sourceAdmitted": False,
        "historicalEligible": False,
        "registryEligible": False,
    }


def test_current_master_first_observation_is_forward_only_and_never_historical():
    value = payload({"twse_current_master": "forward"})
    result = admission.run(value, enabled=True)
    row = next(item for item in result["sourceResults"] if item["sourceSlot"] == "twse_current_master")
    assert row["candidateClass"] == admission.FORWARD
    assert "forward_observation_not_historical_admission" in row["reasonCodes"]
    assert result["forwardObservedOnlyCount"] == 1
    assert result["historicalEvidenceShapeComplete"] is False
    assert result["forwardEligible"] is False


def test_first_seen_retrieved_generated_date_only_headers_and_inference_never_become_history():
    for evidence_class in (
        "unknown", "date_only", "retrieved_at", "generated_at", "http_date",
        "http_last_modified", "catalog_modified", "observation_date",
        "inferred_next_trading_day", "first_seen_backfill",
    ):
        value = payload()
        source = value["sourceEvidence"][0]
        source["availabilityEvidenceClass"] = evidence_class
        source["revisionPublishedAt"] = "2025-01-02" if evidence_class == "date_only" else None
        rehash(value)
        result = admission.run(value, enabled=True)
        row = next(item for item in result["sourceResults"] if item["sourceSlot"] == source["sourceSlot"])
        assert row["candidateClass"] == admission.REJECTED
        assert "prohibited_availability_substitute" in row["reasonCodes"]


def test_current_snapshot_cannot_be_projected_back_even_with_official_timestamp():
    value = payload()
    source = value["sourceEvidence"][0]
    source["coverageClass"] = "current_snapshot"
    source["historyStart"] = None
    source["historyEnd"] = None
    rehash(value)
    row = admission.run(value, enabled=True)["sourceResults"][0]
    assert row["candidateClass"] == admission.REJECTED


def test_identity_effective_availability_and_legal_axes_are_independent():
    mutations = (
        ("datasetId", "wrong-dataset", "identityEvidenceComplete"),
        ("effectiveSemanticsClass", "effective-date-only", "effectiveSemanticsEvidenceComplete"),
        ("publicationSemanticsHash", "0", "availabilityEvidenceComplete"),
        ("termsStatus", "unknown", "legalUseEvidenceComplete"),
    )
    for field, replacement, result_field in mutations:
        value = payload()
        source = value["sourceEvidence"][0]
        source[field] = replacement
        rehash(value)
        result = admission.run(value, enabled=True)
        row = next(item for item in result["sourceResults"] if item["sourceSlot"] == source["sourceSlot"])
        assert row[result_field] is False
        assert row["candidateClass"] == admission.REJECTED


def test_missing_attribution_permissions_partial_pages_counts_or_conflict_fail_closed():
    cases = (
        ("attributionStatus", "unknown"),
        ("legalPermissions", ["automated_access"]),
        ("parsedPages", 0),
        ("rejectedRecords", 1),
        ("conflictStatus", "conflict"),
        ("immutableRevision", False),
        ("appendOnly", False),
    )
    for field, replacement in cases:
        value = payload()
        value["sourceEvidence"][0][field] = replacement
        rehash(value)
        result = admission.run(value, enabled=True)
        assert result["historicalEvidenceShapeComplete"] is False
        assert result["historicalCandidateCount"] == 0


def test_current_and_unregistered_slots_cannot_self_promote_by_rehashing_everything():
    value = payload()
    for source in value["sourceEvidence"]:
        source.update({
            "coverageClass": "complete_history",
            "availabilityEvidenceClass": "official_timezone_timestamp",
            "revisionPublishedAt": "2000-01-01T09:00:00+08:00",
            "observationCompletedAt": None,
            "revisionIdentityScheme": "official-immutable-version",
            "immutableRevision": True,
            "appendOnly": True,
            "correctionPolicy": "append-only-supersedes",
            "conflictStatus": "none",
        })
        source["publicationSemanticsHash"] = hx(source["sourceSlot"] + "-self-claim")
        source["evidenceContentHash"] = hx(source["sourceSlot"] + "-self-content")
    rehash(value)
    result = admission.run(value, enabled=True)
    assert result["contractStructurallyValid"] is True
    assert result["historicalCandidateCount"] == 0
    assert all(row["candidateClass"] == admission.REJECTED for row in result["sourceResults"])


def test_wrong_or_missing_obligation_and_source_slot_never_pass():
    value = payload()
    value["sourceEvidence"][0]["obligationIds"] = []
    rehash(value)
    result = admission.run(value, enabled=True)
    assert result["historicalEvidenceShapeComplete"] is False

    value = payload()
    value["sourceEvidence"].pop()
    rehash(value)
    result = admission.run(value, enabled=True)
    assert result["contractStructurallyValid"] is False
    assert "required_source_slot_missing_or_duplicate" in result["blockers"]

    value = payload()
    value["sourceEvidence"][1] = copy.deepcopy(value["sourceEvidence"][0])
    rehash(value)
    result = admission.run(value, enabled=True)
    assert result["contractStructurallyValid"] is False


def test_root_pin_evidence_hash_and_study_coverage_drift_fail_closed():
    for field, replacement in (
        ("populationPolicyHash", "0" * 64),
        ("node50ContractHash", "1" * 64),
        ("node51ReceiptPolicyHash", "2" * 64),
        ("obligationMatrixHash", "3" * 64),
        ("timezone", "UTC"),
        ("studyFrom", "2030-01-01"),
    ):
        value = payload()
        value[field] = replacement
        result = admission.run(value, enabled=True)
        if field == "studyFrom":
            assert result["historicalEvidenceShapeComplete"] is False
        else:
            assert result["contractStructurallyValid"] is False
    value = payload()
    value["evidenceSetHash"] = "f" * 64
    result = admission.run(value, enabled=True)
    assert "evidence_set_hash_mismatch" in result["blockers"]


def test_all_caller_hashes_and_self_claims_cannot_open_any_authority_flag():
    value = payload()
    value["sourceEvidence"][0]["admitted"] = True
    rehash(value)
    result = admission.run(value, enabled=True)
    assert result["contractStructurallyValid"] is False
    assert result["sourceAdmitted"] is False
    assert result["historicalEligible"] is False


def test_empty_registry_is_exact_and_any_nonempty_registry_is_unsupported():
    path = ROOT / "official_population_source_admission_registry_v1.json"
    committed = json.loads(path.read_text(encoding="utf-8"))
    assert set(committed) == admission.REGISTRY_KEYS
    assert committed == admission.empty_registry()
    assert committed["entries"] == []
    assert population_producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST == frozenset()


def test_registry_append_only_admission_is_intentionally_not_implemented():
    assert "append" not in admission.__dict__
    assert "admit" not in admission.__dict__
    assert admission.empty_registry()["entries"] == []


def test_input_order_is_deterministic_and_output_is_sanitized():
    first_value = payload()
    second_value = copy.deepcopy(first_value)
    second_value["sourceEvidence"].reverse()
    first = admission.run(first_value, enabled=True)
    second = admission.run(second_value, enabled=True)
    assert first == second
    encoded = json.dumps(first, sort_keys=True)
    for forbidden in (
        "https://", "token=", "revisionPublishedAt", "observationCompletedAt",
        "providerLegalIdentityHash", "documentationEvidenceHash", "termsHash",
    ):
        assert forbidden not in encoded


def test_malformed_huge_sensitive_url_and_hostile_inputs_fail_closed():
    cycle = []
    cycle.append(cycle)

    class BadDict(dict):
        def items(self):
            raise RuntimeError("boom")

    class BadList(list):
        def __iter__(self):
            raise RuntimeError("boom")

    for item in (
        {"x": float("nan")}, {"x": {1, 2}}, {1: "bad"}, cycle,
        {"x": 10**10000}, {"x": -(10**10000)}, BadDict(), BadList(),
    ):
        result = admission.evaluate(item)
        assert result["sourceAdmitted"] is False
        assert result["blockers"]
    for key, unsafe in (
        ("raw", [{"x": 1}]), ("url", "https://example.invalid"),
        ("token", "secret"), ("return", 1.0),
    ):
        value = payload()
        value["sourceEvidence"][0][key] = unsafe
        rehash(value)
        result = admission.evaluate(value)
        assert result["contractStructurallyValid"] is False


def test_upstream_runtime_mutation_does_not_change_frozen_pins_or_results():
    before = admission.run(payload(), enabled=True)
    old_hash = population_producer.population_policy_hash
    old_components = population_producer.REQUIRED_COMPONENTS
    old_allowlist = population_producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST
    try:
        population_producer.population_policy_hash = lambda: "f" * 64
        population_producer.REQUIRED_COMPONENTS = ("caller",)
        population_producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST = frozenset({"caller"})
        after = admission.run(payload(), enabled=True)
    finally:
        population_producer.population_policy_hash = old_hash
        population_producer.REQUIRED_COMPONENTS = old_components
        population_producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST = old_allowlist
    assert before == after


def test_module_has_no_network_database_env_clock_subprocess_or_formal_imports():
    path = ROOT / "official_population_source_admission.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported.intersection({
        "scoring", "backtest", "strategy_backtest", "investment_advice_gate",
        "daily_report", "candidate_manifest", "telegram", "requests", "urllib",
        "socket", "subprocess", "os", "supabase", "psycopg", "openai",
    })
    source = path.read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "utcnow" not in source
    for consumer in (
        "authoritative_pit_coverage_certification.py",
        "official_full_market_population.py",
        "official_population_artifact_receipt.py",
        "investment_advice_gate.py",
        "daily_report.py",
    ):
        assert "official_population_source_admission" not in (ROOT / consumer).read_text(encoding="utf-8")


def test_workflow_covers_module_registry_and_tests_without_external_permissions():
    workflow = (ROOT / ".github/workflows/pipeline-safety-validation.yml").read_text(encoding="utf-8")
    assert "official_population_source_admission.py" in workflow
    assert "official_population_source_admission_registry_v1.json" in workflow
    assert "tests/**" in workflow
    assert "contents: read" in workflow
    assert "id-token: write" not in workflow
    assert "attestations: write" not in workflow


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
