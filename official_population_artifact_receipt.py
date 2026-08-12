"""Offline, fixture-only receipt contract for future population artifacts.

This module verifies only deterministic metadata and subject integrity.  It
does not perform cryptographic attestation verification, authenticate an
official source, access GitHub, or admit anything to the PIT trust registry.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import official_full_market_population as population_producer


SCHEMA_VERSION = 1
POLICY_VERSION = "official-population-artifact-receipt-v1"
SUBJECT_FILENAME = "official-population-root-v1.json"
ARTIFACT_NAME = "official-population-root-v1"
REPOSITORY = "clsppy0119-hash/weekly-investment-agent"
HEAD_REF = "refs/heads/main"
ALLOWED_EVENT = "push"
EXPECTED_WORKFLOW_PATH = ".github/workflows/official-population-producer.yml"
MAX_NODES = 25_000
MAX_DEPTH = 12
MAX_STRING = 512
MAX_CANONICAL_BYTES = 1_000_000
MAX_SUBJECT_BYTES = 500_000
MAX_CHUNK_BYTES = 250_000
MAX_ARCHIVE_BYTES = 5_000_000
MAX_INTEGER_ABS = 10**18
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._/-]{1,160}$")

ROOT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "subjectFilename",
    "decisionAsOfHash", "populationPolicyHash", "producerContractHash",
    "sourceContractSetHash", "componentSetHash", "selectedRevisionSetHash",
    "structuralReportDigest", "chunkManifest",
})
SUBJECT_KEYS = frozenset({"filename", "byteSize", "sha256"})
CHUNK_DESCRIPTOR_KEYS = frozenset({"filename", "byteSize", "sha256"})
BUILD_KEYS = frozenset({
    "repositoryId", "repository", "workflowPath", "workflowContentHash",
    "headSha", "headRef", "event", "runId", "runAttempt", "conclusion",
    "artifactId", "artifactName", "artifactDeclaredBytes",
    "artifactActualBytes", "archiveSha256",
})
INPUT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "subject", "rootManifest", "chunks",
    "buildMetadata",
})
REPORT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "mode", "structuralPopulationComplete",
    "officialProducerRegistered", "historicalEligible", "populationPolicyHash",
    "decisionAsOfHash", "inputDigest", "componentCount",
    "universeEntityCount", "universeEntitySetHash", "componentSetHash",
    "selectedRevisionSetHash", "blockers", "artifactDigest",
})
SUMMARY_KEYS = frozenset({
    "schemaVersion", "policyVersion", "producerPolicyVersion",
    "populationPolicyHash", "producerContractHash", "sourceContractSetHash",
    "officialSourceAdmissionCount", "officialProducerRegistered",
    "historicalEligible",
})

STRUCTURAL_CHUNK = "population-structural-report-v1.json"
CONTRACT_CHUNK = "population-contract-summary-v1.json"
ALLOWED_CHUNKS = (CONTRACT_CHUNK, STRUCTURAL_CHUNK)
FIXED_BLOCKERS = (
    "official_source_admission_unregistered",
    "historical_available_at_authority_unregistered",
    "cryptographic_attestation_unverified",
    "trusted_registry_admission_unimplemented",
)
FORBIDDEN_KEYS = frozenset({
    "raw", "rows", "price", "prices", "volume", "score", "rank",
    "return", "returns", "performance", "pnl", "recommendation", "token",
    "secret", "authorization", "cookie", "password", "url", "headers",
    "signatureverified", "attestationverified", "sourcecertified",
    "pitcoveragecertified", "promotioneligible", "adviceenabled",
})
FORBIDDEN_TEXT = (
    "://", "bearer ", "authorization:", "token=", "password=", "cookie=",
    "-----begin ",
)


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return canonical(value).encode("utf-8")


def producer_contract_hash() -> str:
    return PRODUCER_CONTRACT_HASH


def source_contract_set_hash() -> str:
    return SOURCE_CONTRACT_SET_HASH


SOURCE_CONTRACT_SET_HASH = digest(population_producer.SOURCE_CONTRACTS)
PINNED_PRODUCER_SCHEMA_VERSION = population_producer.SCHEMA_VERSION
PINNED_PRODUCER_POLICY_VERSION = population_producer.POLICY_VERSION
PINNED_POPULATION_POLICY_HASH = population_producer.population_policy_hash()
PINNED_REQUIRED_COMPONENTS = tuple(population_producer.REQUIRED_COMPONENTS)
PINNED_SECURITY_SCHEMA_HASH = population_producer.security_schema_hash()
PINNED_SOURCE_ADMISSION_COUNT = len(
    population_producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST
)
PRODUCER_CONTRACT_HASH = digest({
    "producerPolicyVersion": PINNED_PRODUCER_POLICY_VERSION,
    "populationPolicyHash": PINNED_POPULATION_POLICY_HASH,
    "requiredComponents": list(PINNED_REQUIRED_COMPONENTS),
    "sourceContractSetHash": SOURCE_CONTRACT_SET_HASH,
    "securitySchemaHash": PINNED_SECURITY_SCHEMA_HASH,
    "officialSourceAdmissionCount": PINNED_SOURCE_ADMISSION_COUNT,
})


def _json_domain(value: Any, depth: int = 0, count: list[int] | None = None) -> bool:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_NODES or depth > MAX_DEPTH:
        return False
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int):
        return not isinstance(value, bool) and abs(value) <= MAX_INTEGER_ABS
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= MAX_STRING and all(ord(char) >= 32 for char in value)
    if isinstance(value, list):
        return all(_json_domain(item, depth + 1, count) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and len(key) <= 100
            and _json_domain(item, depth + 1, count)
            for key, item in value.items()
        )
    return False


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            folded = key.casefold().replace("_", "")
            if folded in FORBIDDEN_KEYS or _contains_forbidden(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    if isinstance(value, str):
        folded = value.casefold()
        return any(marker in folded for marker in FORBIDDEN_TEXT)
    return False


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _safe_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and SAFE_NAME.fullmatch(value) is not None
        and not value.startswith("/")
        and "\\" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _build_metadata_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != BUILD_KEYS:
        return False
    return (
        _positive_int(value.get("repositoryId"))
        and value.get("repository") == REPOSITORY
        and value.get("workflowPath") == EXPECTED_WORKFLOW_PATH
        and _hex64(value.get("workflowContentHash"))
        and isinstance(value.get("headSha"), str)
        and HEX40.fullmatch(value["headSha"]) is not None
        and value.get("headRef") == HEAD_REF
        and value.get("event") == ALLOWED_EVENT
        and _positive_int(value.get("runId"))
        and _positive_int(value.get("runAttempt"))
        and value.get("conclusion") == "success"
        and _positive_int(value.get("artifactId"))
        and value.get("artifactName") == ARTIFACT_NAME
        and _positive_int(value.get("artifactDeclaredBytes"))
        and value.get("artifactDeclaredBytes") <= MAX_ARCHIVE_BYTES
        and value.get("artifactDeclaredBytes") == value.get("artifactActualBytes")
        and _hex64(value.get("archiveSha256"))
    )


def _structural_report_valid(report: Any) -> bool:
    if not isinstance(report, dict) or set(report) != REPORT_KEYS:
        return False
    expected_digest = digest({
        key: value for key, value in report.items() if key != "artifactDigest"
    })
    return (
        report.get("schemaVersion") == PINNED_PRODUCER_SCHEMA_VERSION
        and report.get("policyVersion") == PINNED_PRODUCER_POLICY_VERSION
        and report.get("mode") == "research_only"
        and report.get("structuralPopulationComplete") is True
        and report.get("officialProducerRegistered") is False
        and report.get("historicalEligible") is False
        and report.get("populationPolicyHash") == PINNED_POPULATION_POLICY_HASH
        and _hex64(report.get("decisionAsOfHash"))
        and _hex64(report.get("inputDigest"))
        and report.get("componentCount") == len(PINNED_REQUIRED_COMPONENTS)
        and _positive_int(report.get("universeEntityCount"))
        and _hex64(report.get("universeEntitySetHash"))
        and _hex64(report.get("componentSetHash"))
        and _hex64(report.get("selectedRevisionSetHash"))
        and report.get("blockers") == [
            "official_source_admission_unregistered",
            "historical_available_at_authority_unregistered",
        ]
        and report.get("artifactDigest") == expected_digest
    )


def _contract_summary_valid(summary: Any) -> bool:
    return (
        isinstance(summary, dict)
        and set(summary) == SUMMARY_KEYS
        and summary.get("schemaVersion") == SCHEMA_VERSION
        and summary.get("policyVersion") == POLICY_VERSION
        and summary.get("producerPolicyVersion") == PINNED_PRODUCER_POLICY_VERSION
        and summary.get("populationPolicyHash") == PINNED_POPULATION_POLICY_HASH
        and summary.get("producerContractHash") == producer_contract_hash()
        and summary.get("sourceContractSetHash") == source_contract_set_hash()
        and summary.get("officialSourceAdmissionCount") == PINNED_SOURCE_ADMISSION_COUNT == 0
        and summary.get("officialProducerRegistered") is False
        and summary.get("historicalEligible") is False
    )


def _descriptor(value: Any, filename: str, payload: Any) -> bool:
    encoded = canonical_bytes(payload)
    return (
        isinstance(value, dict)
        and set(value) == CHUNK_DESCRIPTOR_KEYS
        and value.get("filename") == filename
        and len(encoded) <= MAX_CHUNK_BYTES
        and value.get("byteSize") == len(encoded)
        and value.get("sha256") == hashlib.sha256(encoded).hexdigest()
    )


def _root_valid(root: Any, chunks: Any) -> bool:
    if not isinstance(root, dict) or set(root) != ROOT_KEYS:
        return False
    if not isinstance(chunks, dict) or set(chunks) != set(ALLOWED_CHUNKS):
        return False
    manifest = root.get("chunkManifest")
    if not isinstance(manifest, list) or len(manifest) != len(ALLOWED_CHUNKS):
        return False
    ordered = sorted(manifest, key=lambda row: row.get("filename", "") if isinstance(row, dict) else "")
    if manifest != ordered:
        return False
    descriptors = {row.get("filename"): row for row in manifest if isinstance(row, dict)}
    report = chunks.get(STRUCTURAL_CHUNK)
    summary = chunks.get(CONTRACT_CHUNK)
    return (
        root.get("schemaVersion") == SCHEMA_VERSION
        and root.get("policyVersion") == POLICY_VERSION
        and root.get("subjectFilename") == SUBJECT_FILENAME
        and _hex64(root.get("decisionAsOfHash"))
        and root.get("populationPolicyHash") == PINNED_POPULATION_POLICY_HASH
        and root.get("producerContractHash") == producer_contract_hash()
        and root.get("sourceContractSetHash") == source_contract_set_hash()
        and _hex64(root.get("componentSetHash"))
        and _hex64(root.get("selectedRevisionSetHash"))
        and _hex64(root.get("structuralReportDigest"))
        and _structural_report_valid(report)
        and _contract_summary_valid(summary)
        and root.get("decisionAsOfHash") == report.get("decisionAsOfHash")
        and root.get("componentSetHash") == report.get("componentSetHash")
        and root.get("selectedRevisionSetHash") == report.get("selectedRevisionSetHash")
        and root.get("structuralReportDigest") == report.get("artifactDigest")
        and set(descriptors) == set(ALLOWED_CHUNKS)
        and all(_descriptor(descriptors[name], name, chunks[name]) for name in ALLOWED_CHUNKS)
    )


def _subject_valid(subject: Any, root: Any) -> bool:
    encoded = canonical_bytes(root)
    return (
        isinstance(subject, dict)
        and set(subject) == SUBJECT_KEYS
        and subject.get("filename") == SUBJECT_FILENAME
        and len(encoded) <= MAX_SUBJECT_BYTES
        and subject.get("byteSize") == len(encoded)
        and subject.get("sha256") == hashlib.sha256(encoded).hexdigest()
    )


def _report(blockers: list[str], *, integrity: bool = False,
            build_metadata_complete: bool = False,
            subject_digest_matched: bool = False) -> dict[str, Any]:
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "receiptIntegrityVerified": integrity,
        "buildMetadataComplete": build_metadata_complete,
        "subjectDigestMatched": subject_digest_matched,
        "attestationVerified": False,
        "officialSourceAuthenticated": False,
        "populationCompleteCertified": False,
        "historicalEligible": False,
        "pitCoverageCertified": False,
        "strategyValidated": False,
        "promotionEligible": False,
        "adviceEnabled": False,
        "trustedReceiptEligible": False,
        "registryEligible": False,
        "formalGateAttached": False,
        "blockers": list(dict.fromkeys([*FIXED_BLOCKERS, *blockers])),
    }
    report["receiptDigest"] = digest(report)
    return report


def _verify(value: Any) -> dict[str, Any]:
    if not _json_domain(value) or not isinstance(value, dict):
        return _report(["input_not_bounded_json"])
    if len(canonical_bytes(value)) > MAX_CANONICAL_BYTES:
        return _report(["input_too_large"])
    if set(value) != INPUT_KEYS or _contains_forbidden(value):
        return _report(["input_contract_invalid"])
    if value.get("schemaVersion") != SCHEMA_VERSION \
            or value.get("policyVersion") != POLICY_VERSION:
        return _report(["root_contract_invalid"])
    build_valid = _build_metadata_valid(value.get("buildMetadata"))
    root_valid = _root_valid(value.get("rootManifest"), value.get("chunks"))
    subject_valid = _subject_valid(value.get("subject"), value.get("rootManifest"))
    blockers: list[str] = []
    if not build_valid:
        blockers.append("build_metadata_invalid")
    if not root_valid:
        blockers.append("root_or_chunk_integrity_invalid")
    if not subject_valid:
        blockers.append("subject_digest_mismatch")
    integrity = build_valid and root_valid and subject_valid
    return _report(
        blockers, integrity=integrity,
        build_metadata_complete=build_valid,
        subject_digest_matched=subject_valid,
    )


def verify(value: Any) -> dict[str, Any]:
    """Public fail-closed boundary; malformed input never escapes."""
    try:
        return _verify(value)
    except Exception:
        return _report(["input_fail_closed"])


def run(value: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    """Default-off pure boundary; disabled mode does not inspect ``value``."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "mode": "disabled",
            "receiptIntegrityVerified": False,
            "attestationVerified": False,
            "trustedReceiptEligible": False,
            "registryEligible": False,
        }
    return verify(value)
