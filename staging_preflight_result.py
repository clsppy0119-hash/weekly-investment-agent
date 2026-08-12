"""Offline validator for a sanitized staging preflight result package.

The package is evidence that a human ran the approved *read-only* catalog
preflight.  It is not database, PIT, backtest, or investment evidence.  This
module is intentionally pure and never reads files, environment variables,
credentials, clocks, databases, or the network.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = 1
SCOPE = "b2b2b_staging_preflight_result_v1"
MAIN_SHA = "54849db0958a5bd20ccbb87a3884c3c3dd9a9a6a"
AUTHORITY_SOURCE_PIN = "8850d8d8cd65772f1f0457d7f6cbc3288c2150b8f6f4f28c33ad70e9177e61fb"
DRY_RUN_SOURCE_PIN = "a19b9a927e2b773591873abd20783b53d630691d3f4791a386adac622127d3f9"
QUERY_PIN = "a4f9734da90891793e74ac9c26b8b5217cb2a48be2b4c567b1aa8f64da84ac61"

PACKAGE_KEYS = {
    "schemaVersion", "mode", "diagnosticOnly", "scope", "environment",
    "mainSha", "authoritySourcePin", "dryRunSourcePin", "queryPin",
    "authorityContractDigest", "stagingRefHash", "productionRefHash",
    "preflightSummary", "manualAttestations", "retention", "audit",
}
SUMMARY_KEYS = {
    "executorRoleIsPostgres", "transactionReadOnly", "pgcryptoNamespaceExact",
    "digestSignaturePresent", "targetRoleCount", "targetSchemaCount",
    "targetRelationCount", "targetRoutineCount",
    "privateSchemaInAuthenticatorOverride", "privatePublicationExposureCount",
}
SUMMARY_EXPECTED = {
    "executorRoleIsPostgres": True,
    "transactionReadOnly": True,
    "pgcryptoNamespaceExact": True,
    "digestSignaturePresent": True,
    "targetRoleCount": 0,
    "targetSchemaCount": 0,
    "targetRelationCount": 0,
    "targetRoutineCount": 0,
    "privateSchemaInAuthenticatorOverride": False,
    "privatePublicationExposureCount": 0,
}
ATTESTATION_EXPECTED = {
    "stagingIdentityVerified": True,
    "productionProjectNotOpened": True,
    "readOnlyTransactionUsed": True,
    "statementExecutedExactlyOnce": True,
    "migrationExecuted": False,
    "rpcInvoked": False,
    "applicationRowsRead": False,
    "databaseWritesObserved": False,
    "rawConsoleOutputPersisted": False,
}
RETENTION_EXPECTED = {
    "metadataOnly": True,
    "rawOutputRetained": False,
    "retentionDays": 30,
    "deleteOnExpiry": True,
}
AUDIT_KEYS = {"authorityTicketHash", "operatorAttestationHash", "capturedAt"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
UNSAFE_RE = re.compile(
    r"(?i)(?:https?://|postgres(?:ql)?://|eyj[a-z0-9_-]*\.[a-z0-9_-]+\.[a-z0-9_-]+|"
    r"service[_-]?role|password|authorization|bearer|cookie|apikey|api[_-]?key|"
    r"\b(?:select|insert|update|delete|create|alter|drop|grant|revoke|begin|rollback)\b|"
    r"[;\r\n])"
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _unsafe(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_unsafe(k) or _unsafe(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_unsafe(v) for v in value)
    return isinstance(value, str) and UNSAFE_RE.search(value) is not None


def _result(*, blockers: list[str], package_digest: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "research_only",
        "diagnosticOnly": True,
        "readyForTransactionDryRun": not blockers,
        "packageDigest": package_digest,
        "blockers": sorted(set(blockers)),
        "retentionClass": "metadata_only_30_days",
        "limitations": [
            "manual_attestations_not_independently_proven",
            "catalog_preflight_only",
            "not_pit_backtest_or_investment_evidence",
        ],
    }


def validate(package: Any, *, enabled: bool = False) -> dict[str, Any]:
    """Return a bounded deterministic verdict without any side effects."""
    if not enabled:
        output = _result(blockers=["feature_disabled"])
        output["mode"] = "disabled"
        return output
    if not isinstance(package, dict) or set(package) != PACKAGE_KEYS:
        return _result(blockers=["package_contract_invalid"])
    if _unsafe(package):
        return _result(blockers=["sensitive_raw_or_sql_value_forbidden"])

    digest = _digest(package)
    blockers: list[str] = []
    if not (
        package.get("schemaVersion") == SCHEMA_VERSION
        and package.get("mode") == "research_only"
        and package.get("diagnosticOnly") is True
        and package.get("scope") == SCOPE
        and package.get("environment") == "staging"
        and package.get("mainSha") == MAIN_SHA
        and SHA_RE.fullmatch(str(package.get("mainSha", ""))) is not None
        and package.get("authoritySourcePin") == AUTHORITY_SOURCE_PIN
        and package.get("dryRunSourcePin") == DRY_RUN_SOURCE_PIN
        and package.get("queryPin") == QUERY_PIN
    ):
        blockers.append("identity_or_pin_invalid")

    for name in ("authorityContractDigest", "stagingRefHash", "productionRefHash"):
        if HASH_RE.fullmatch(str(package.get(name, ""))) is None:
            blockers.append("hash_contract_invalid")
            break
    if package.get("stagingRefHash") == package.get("productionRefHash"):
        blockers.append("project_separation_unverified")

    summary = package.get("preflightSummary")
    if not isinstance(summary, dict) or set(summary) != SUMMARY_KEYS or summary != SUMMARY_EXPECTED:
        blockers.append("preflight_summary_failed")
    if package.get("manualAttestations") != ATTESTATION_EXPECTED:
        blockers.append("manual_attestation_failed")
    if package.get("retention") != RETENTION_EXPECTED:
        blockers.append("retention_policy_invalid")

    audit = package.get("audit")
    if not isinstance(audit, dict) or set(audit) != AUDIT_KEYS:
        blockers.append("audit_contract_invalid")
    elif (
        HASH_RE.fullmatch(str(audit.get("authorityTicketHash", ""))) is None
        or HASH_RE.fullmatch(str(audit.get("operatorAttestationHash", ""))) is None
        or UTC_RE.fullmatch(str(audit.get("capturedAt", ""))) is None
    ):
        blockers.append("audit_contract_invalid")

    return _result(blockers=blockers, package_digest=digest)
