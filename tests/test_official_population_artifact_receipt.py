import copy
import inspect
import json
import math
import unittest
from pathlib import Path

import authoritative_pit_coverage_certification as certification
import official_full_market_population as producer
import official_population_artifact_receipt as receipt
from tests import test_official_full_market_population as population_fixtures


ROOT = Path(__file__).resolve().parents[1]


class Explodes:
    def __getattribute__(self, name):
        raise AssertionError("disabled receipt inspected its input")


class BadDict(dict):
    def items(self):
        raise RuntimeError("hostile dict iteration")


class BadList(list):
    def __iter__(self):
        raise RuntimeError("hostile list iteration")


def _summary():
    return {
        "schemaVersion": receipt.SCHEMA_VERSION,
        "policyVersion": receipt.POLICY_VERSION,
        "producerPolicyVersion": receipt.PINNED_PRODUCER_POLICY_VERSION,
        "populationPolicyHash": receipt.PINNED_POPULATION_POLICY_HASH,
        "producerContractHash": receipt.producer_contract_hash(),
        "sourceContractSetHash": receipt.source_contract_set_hash(),
        "officialSourceAdmissionCount": 0,
        "officialProducerRegistered": False,
        "historicalEligible": False,
    }


def _descriptor(filename, value):
    encoded = receipt.canonical_bytes(value)
    return {
        "filename": filename,
        "byteSize": len(encoded),
        "sha256": receipt.hashlib.sha256(encoded).hexdigest(),
    }


def bundle():
    structural = producer.run(population_fixtures.payload(), enabled=True)
    assert structural["structuralPopulationComplete"] is True
    chunks = {
        receipt.CONTRACT_CHUNK: _summary(),
        receipt.STRUCTURAL_CHUNK: structural,
    }
    root = {
        "schemaVersion": receipt.SCHEMA_VERSION,
        "policyVersion": receipt.POLICY_VERSION,
        "subjectFilename": receipt.SUBJECT_FILENAME,
        "decisionAsOfHash": structural["decisionAsOfHash"],
        "populationPolicyHash": receipt.PINNED_POPULATION_POLICY_HASH,
        "producerContractHash": receipt.producer_contract_hash(),
        "sourceContractSetHash": receipt.source_contract_set_hash(),
        "componentSetHash": structural["componentSetHash"],
        "selectedRevisionSetHash": structural["selectedRevisionSetHash"],
        "structuralReportDigest": structural["artifactDigest"],
        "chunkManifest": [
            _descriptor(name, chunks[name]) for name in receipt.ALLOWED_CHUNKS
        ],
    }
    subject_bytes = receipt.canonical_bytes(root)
    return {
        "schemaVersion": receipt.SCHEMA_VERSION,
        "policyVersion": receipt.POLICY_VERSION,
        "subject": {
            "filename": receipt.SUBJECT_FILENAME,
            "byteSize": len(subject_bytes),
            "sha256": receipt.hashlib.sha256(subject_bytes).hexdigest(),
        },
        "rootManifest": root,
        "chunks": chunks,
        "buildMetadata": {
            "repositoryId": 987654321,
            "repository": receipt.REPOSITORY,
            "workflowPath": receipt.EXPECTED_WORKFLOW_PATH,
            "workflowContentHash": "1" * 64,
            "headSha": "2" * 40,
            "headRef": receipt.HEAD_REF,
            "event": receipt.ALLOWED_EVENT,
            "runId": 123456789,
            "runAttempt": 1,
            "conclusion": "success",
            "artifactId": 987654321,
            "artifactName": receipt.ARTIFACT_NAME,
            "artifactDeclaredBytes": 4096,
            "artifactActualBytes": 4096,
            "archiveSha256": "3" * 64,
        },
    }


def _refresh(value):
    structural = value["chunks"][receipt.STRUCTURAL_CHUNK]
    if isinstance(structural, dict) and set(structural) == receipt.REPORT_KEYS:
        structural["artifactDigest"] = producer.digest({
            key: child for key, child in structural.items()
            if key != "artifactDigest"
        })
        value["rootManifest"]["decisionAsOfHash"] = structural["decisionAsOfHash"]
        value["rootManifest"]["componentSetHash"] = structural["componentSetHash"]
        value["rootManifest"]["selectedRevisionSetHash"] = structural["selectedRevisionSetHash"]
        value["rootManifest"]["structuralReportDigest"] = structural["artifactDigest"]
    value["rootManifest"]["chunkManifest"] = [
        _descriptor(name, value["chunks"][name]) for name in receipt.ALLOWED_CHUNKS
    ]
    subject_bytes = receipt.canonical_bytes(value["rootManifest"])
    value["subject"] = {
        "filename": receipt.SUBJECT_FILENAME,
        "byteSize": len(subject_bytes),
        "sha256": receipt.hashlib.sha256(subject_bytes).hexdigest(),
    }


def test_default_off_does_not_inspect_input_and_has_no_io():
    result = receipt.run(Explodes(), enabled=False)
    assert result == {
        "schemaVersion": 1,
        "policyVersion": receipt.POLICY_VERSION,
        "mode": "disabled",
        "receiptIntegrityVerified": False,
        "attestationVerified": False,
        "trustedReceiptEligible": False,
        "registryEligible": False,
    }


def test_complete_fixture_only_verifies_integrity_never_authority_or_pit():
    result = receipt.run(bundle(), enabled=True)
    assert result["receiptIntegrityVerified"] is True
    assert result["buildMetadataComplete"] is True
    assert result["subjectDigestMatched"] is True
    for key in (
        "attestationVerified", "officialSourceAuthenticated",
        "populationCompleteCertified", "historicalEligible",
        "pitCoverageCertified", "strategyValidated", "promotionEligible",
        "adviceEnabled", "trustedReceiptEligible", "registryEligible",
        "formalGateAttached",
    ):
        assert result[key] is False
    assert set(receipt.FIXED_BLOCKERS).issubset(result["blockers"])


def test_build_identity_is_exact_and_pr_fork_or_failed_run_fails_closed():
    mutations = {
        "repository": "someone/fork",
        "workflowPath": ".github/workflows/other.yml",
        "headRef": "refs/pull/1/merge",
        "event": "pull_request",
        "runAttempt": 0,
        "conclusion": "failure",
        "artifactName": "daily-investment-report",
        "artifactActualBytes": 4095,
    }
    for key, replacement in mutations.items():
        value = bundle()
        value["buildMetadata"][key] = replacement
        result = receipt.run(value, enabled=True)
        assert result["receiptIntegrityVerified"] is False
        assert "build_metadata_invalid" in result["blockers"]


def test_subject_and_chunk_byte_mutations_fail_without_matching_rehash():
    value = bundle()
    value["subject"]["sha256"] = "f" * 64
    result = receipt.run(value, enabled=True)
    assert result["subjectDigestMatched"] is False
    assert "subject_digest_mismatch" in result["blockers"]

    value = bundle()
    value["chunks"][receipt.STRUCTURAL_CHUNK]["componentSetHash"] = "f" * 64
    result = receipt.run(value, enabled=True)
    assert result["receiptIntegrityVerified"] is False
    assert "root_or_chunk_integrity_invalid" in result["blockers"]

    value = bundle()
    value["rootManifest"]["chunkManifest"][0]["byteSize"] += 1
    _refresh_subject_only(value)
    result = receipt.run(value, enabled=True)
    assert result["receiptIntegrityVerified"] is False


def _refresh_subject_only(value):
    subject_bytes = receipt.canonical_bytes(value["rootManifest"])
    value["subject"] = {
        "filename": receipt.SUBJECT_FILENAME,
        "byteSize": len(subject_bytes),
        "sha256": receipt.hashlib.sha256(subject_bytes).hexdigest(),
    }


def test_rehashing_caller_claims_never_authenticates_source_or_attestation():
    value = bundle()
    structural = value["chunks"][receipt.STRUCTURAL_CHUNK]
    structural["componentSetHash"] = "a" * 64
    structural["selectedRevisionSetHash"] = "b" * 64
    _refresh(value)
    result = receipt.run(value, enabled=True)
    assert result["receiptIntegrityVerified"] is True
    assert result["attestationVerified"] is False
    assert result["officialSourceAuthenticated"] is False
    assert result["pitCoverageCertified"] is False
    assert result["registryEligible"] is False


def test_producer_cannot_self_promote_even_when_all_hashes_are_recomputed():
    value = bundle()
    report = value["chunks"][receipt.STRUCTURAL_CHUNK]
    report["officialProducerRegistered"] = True
    report["historicalEligible"] = True
    _refresh(value)
    result = receipt.run(value, enabled=True)
    assert result["receiptIntegrityVerified"] is False
    assert result["officialSourceAuthenticated"] is False

    value = bundle()
    value["chunks"][receipt.CONTRACT_CHUNK]["officialSourceAdmissionCount"] = 1
    _refresh(value)
    result = receipt.run(value, enabled=True)
    assert result["receiptIntegrityVerified"] is False


def test_caller_verification_flags_and_sensitive_or_url_content_are_rejected():
    value = bundle()
    value["attestationVerified"] = True
    result = receipt.run(value, enabled=True)
    assert result["receiptIntegrityVerified"] is False
    assert "input_contract_invalid" in result["blockers"]

    for replacement in (
        "https://example.invalid/workflow", "Bearer abc", "token=abc",
    ):
        value = bundle()
        value["buildMetadata"]["workflowPath"] = replacement
        result = receipt.run(value, enabled=True)
        assert result["receiptIntegrityVerified"] is False
        assert "input_contract_invalid" in result["blockers"]


def test_malformed_non_json_nan_cycle_and_oversize_fail_closed():
    cycle = {}
    cycle["self"] = cycle
    values = (
        {"x": math.nan}, {"x": {1, 2}}, {1: "bad"}, cycle,
        {"x": "a" * (receipt.MAX_STRING + 1)},
        {"x": 10**10_000}, {"x": -(10**10_000)},
    )
    for value in values:
        result = receipt.run(value, enabled=True)
        assert result["mode"] == "research_only"
        assert result["receiptIntegrityVerified"] is False
        direct = receipt.verify(value)
        assert direct["mode"] == "research_only"
        assert direct["receiptIntegrityVerified"] is False


def test_hostile_container_runtime_errors_fail_closed_at_both_public_boundaries():
    for value in (BadDict(), BadList()):
        direct = receipt.verify(value)
        wrapped = receipt.run(value, enabled=True)
        for result in (direct, wrapped):
            assert result["mode"] == "research_only"
            assert result["receiptIntegrityVerified"] is False
            assert "input_fail_closed" in result["blockers"]

    value = bundle()
    value["buildMetadata"]["artifactDeclaredBytes"] = receipt.MAX_ARCHIVE_BYTES + 1
    value["buildMetadata"]["artifactActualBytes"] = receipt.MAX_ARCHIVE_BYTES + 1
    result = receipt.run(value, enabled=True)
    assert result["receiptIntegrityVerified"] is False
    assert "build_metadata_invalid" in result["blockers"]


def test_all_producer_runtime_mutation_cannot_change_frozen_receipt_pins():
    value = bundle()
    before_source = receipt.source_contract_set_hash()
    before_producer = receipt.producer_contract_hash()
    original = copy.deepcopy(producer.SOURCE_CONTRACTS)
    original_schema_version = producer.SCHEMA_VERSION
    original_policy_version = producer.POLICY_VERSION
    original_components = producer.REQUIRED_COMPONENTS
    original_allowlist = producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST
    original_policy_hash = producer.population_policy_hash
    original_schema_hash = producer.security_schema_hash
    try:
        producer.SCHEMA_VERSION = 999
        producer.POLICY_VERSION = "caller-mutated"
        producer.REQUIRED_COMPONENTS = ("caller",)
        producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST = frozenset({"caller"})
        producer.population_policy_hash = lambda: "f" * 64
        producer.security_schema_hash = lambda: "e" * 64
        producer.SOURCE_CONTRACTS["twse_active"]["dataset"] = "caller-mutated"
        producer.SOURCE_CONTRACTS.clear()
        assert receipt.source_contract_set_hash() == before_source
        assert receipt.producer_contract_hash() == before_producer
        result = receipt.run(value, enabled=True)
        assert result["receiptIntegrityVerified"] is True
        assert result["attestationVerified"] is False
    finally:
        producer.SCHEMA_VERSION = original_schema_version
        producer.POLICY_VERSION = original_policy_version
        producer.REQUIRED_COMPONENTS = original_components
        producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST = original_allowlist
        producer.population_policy_hash = original_policy_hash
        producer.security_schema_hash = original_schema_hash
        producer.SOURCE_CONTRACTS.clear()
        producer.SOURCE_CONTRACTS.update(original)


def test_policy_schema_pin_drift_and_extra_or_missing_chunks_fail_closed():
    value = bundle()
    value["rootManifest"]["populationPolicyHash"] = "f" * 64
    _refresh_subject_only(value)
    assert receipt.run(value, enabled=True)["receiptIntegrityVerified"] is False

    value = bundle()
    del value["chunks"][receipt.CONTRACT_CHUNK]
    assert receipt.run(value, enabled=True)["receiptIntegrityVerified"] is False

    value = bundle()
    value["chunks"]["extra.json"] = {}
    assert receipt.run(value, enabled=True)["receiptIntegrityVerified"] is False


def test_output_is_small_sanitized_and_deterministic():
    first = receipt.run(bundle(), enabled=True)
    second = receipt.run(copy.deepcopy(bundle()), enabled=True)
    assert first == second
    assert first["receiptDigest"] == receipt.digest({
        key: value for key, value in first.items() if key != "receiptDigest"
    })
    encoded = json.dumps(first, sort_keys=True).lower()
    for forbidden in (
        "repositoryid", "headsha", "runid", "artifactid", "archive",
        "securityid", "issuerid", "token", "https://",
    ):
        assert forbidden not in encoded


def test_module_is_offline_and_has_no_formal_or_crypto_shell_imports():
    source = inspect.getsource(receipt)
    imports = {
        line.strip() for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    }
    forbidden = (
        "os", "pathlib", "urllib", "requests", "httpx", "socket",
        "subprocess", "supabase", "psycopg", "scoring", "backtest",
        "investment_advice_gate", "daily_report", "candidate_manifest",
        "promotion", "telegram", "authoritative_pit_coverage_certification",
    )
    assert not any(any(name in line for name in forbidden) for line in imports)
    assert "gh attestation" not in source
    assert "actions/attest" not in source


def test_node49_registry_and_formal_consumer_remain_disconnected():
    certification_source = inspect.getsource(certification)
    assert "official_population_artifact_receipt" not in certification_source
    registry = json.loads((ROOT / "trusted_pit_bundle_registry_v1.json").read_text("utf-8"))
    assert registry["entries"] == []
    assert certification.OFFICIAL_UNIVERSE_PRODUCER_ALLOWLIST == frozenset()


def test_pipeline_safety_workflow_covers_receipt_without_oidc_or_attestation_write():
    workflow = (ROOT / ".github/workflows/pipeline-safety-validation.yml").read_text("utf-8")
    assert '"official_population_artifact_receipt.py"' in workflow
    assert "official_population_artifact_receipt.py" in workflow.split(
        "- name: Run deterministic safety tests", 1
    )[0]
    assert "id-token: write" not in workflow
    assert "attestations: write" not in workflow
    assert "actions/attest" not in workflow


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
