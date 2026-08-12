"""Offline B2B2A validator for a future staging preflight authority package.

This module is deliberately pure: callers provide an in-memory fixture and
receive a bounded research-only verdict.  It never opens files, reads process
state, connects to a database, or executes SQL.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = 1
SCOPE = "b2b2a_staging_read_only_preflight_v1"
MAIN_SHA = "00496233eac25a8c25b8ec37520bdb9d09515d34"
B2A2_SQL_PIN = "d85f00aad322e59d79621db1434098713bbaf4b7be9c828d530f10d04f84505f"
B2A2_SEMANTIC_PIN = "0b52443aea14471851775b8da705cdb332319aa1daa18c2b6916209b5d79132c"
B2B1_PREFLIGHT_PIN = "7377ac324c589bd1ae7504bf3f99c0648c519d5e1e5febca6e9a46d3fd757779"
B2B1_VALIDATOR_PIN = "4d0bcb6e524cf790368265a1dfa5ce8870304399a87314b73b1ad4629b6a844e"

PACKAGE_KEYS = {
    "schemaVersion", "mode", "diagnosticOnly", "scope", "environment",
    "mainSha", "b2a2SqlPin", "b2a2SemanticPin", "b2b1PreflightPin",
    "b2b1ValidatorPin", "stagingRefHash", "productionRefHash",
    "authorityTicketHash", "sqlSummary", "manualAttestations",
    "noWriteAttestations",
}
SQL_KEYS = {
    "schemaVersion", "executorRoleIsPostgres", "transactionReadOnly",
    "pgcryptoNamespaceExact", "digestSignaturePresent", "targetRoleCount",
    "targetSchemaCount", "targetRelationCount", "targetRoutineCount",
    "authenticatorOverrideKnown", "privateSchemaInAuthenticatorOverride",
    "privateRuntimeGrantCount", "privateViewExposureCount",
    "privateRoutineExposureCount", "privatePublicationExposureCount",
}
MANUAL_EXPECTED = {
    "dashboardExposureVerified": True,
    "privateSchemaInDashboardExposed": False,
    "authenticatorOverrideAbsentOrSafe": True,
    "formalIsolationVerified": True,
    "stagingProjectIdentityVerified": True,
    "productionProjectNotOpened": True,
    "sqlTextPinVerified": True,
    "sqlExecutedExactlyOnce": True,
}
NO_WRITE_EXPECTED = {
    "migrationExecuted": False,
    "rpcInvoked": False,
    "applicationRowsRead": False,
    "databaseWritesObserved": False,
    "secretsCopied": False,
    "rawConsoleOutputPersisted": False,
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UNSAFE_TEXT_RE = re.compile(
    r"(?i)(?:https?://|postgres(?:ql)?://|eyj[a-z0-9_-]*\.[a-z0-9_-]+\.[a-z0-9_-]+|"
    r"service[_-]?role|password|authorization|bearer|cookie|\b(?:select|insert|update|"
    r"delete|create|alter|drop|grant|revoke)\b|[;\r\n])"
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _contains_unsafe_text(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_unsafe_text(key) or _contains_unsafe_text(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_unsafe_text(item) for item in value)
    return isinstance(value, str) and UNSAFE_TEXT_RE.search(value) is not None


def _blocked(*codes: str, digest: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "research_only",
        "diagnosticOnly": True,
        "ready": False,
        "contractDigest": digest,
        "blockers": sorted(set(codes)),
        "limitations": [
            "offline_fixture_validation_only",
            "manual_attestations_not_independently_proven",
            "not_investment_or_pit_evidence",
        ],
    }


def validate(package: Any, *, enabled: bool = False) -> dict[str, Any]:
    """Validate one exact package and return a bounded, non-sensitive result."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION, "mode": "disabled",
            "diagnosticOnly": True, "ready": False, "contractDigest": None,
            "blockers": ["feature_disabled"],
            "limitations": ["offline_fixture_validation_only"],
        }
    if not isinstance(package, dict) or set(package) != PACKAGE_KEYS:
        return _blocked("package_contract_invalid")
    if _contains_unsafe_text(package):
        return _blocked("sensitive_or_raw_value_forbidden")

    safe_digest = _digest(package)
    identity_ok = (
        package.get("schemaVersion") == SCHEMA_VERSION
        and package.get("mode") == "research_only"
        and package.get("diagnosticOnly") is True
        and package.get("scope") == SCOPE
        and package.get("environment") == "staging"
        and isinstance(package.get("mainSha"), str)
        and SHA_RE.fullmatch(package["mainSha"]) is not None
        and package["mainSha"] == MAIN_SHA
        and package.get("b2a2SqlPin") == B2A2_SQL_PIN
        and package.get("b2a2SemanticPin") == B2A2_SEMANTIC_PIN
        and package.get("b2b1PreflightPin") == B2B1_PREFLIGHT_PIN
        and package.get("b2b1ValidatorPin") == B2B1_VALIDATOR_PIN
        and isinstance(package.get("authorityTicketHash"), str)
        and HASH_RE.fullmatch(package["authorityTicketHash"]) is not None
    )
    blockers: list[str] = []
    if not identity_ok:
        blockers.append("identity_or_pin_invalid")

    staging_hash = package.get("stagingRefHash")
    production_hash = package.get("productionRefHash")
    if (not isinstance(staging_hash, str) or HASH_RE.fullmatch(staging_hash) is None
            or not isinstance(production_hash, str) or HASH_RE.fullmatch(production_hash) is None
            or staging_hash == production_hash):
        blockers.append("project_separation_unverified")

    sql = package.get("sqlSummary")
    if not _valid_sql_summary(sql):
        blockers.append("sql_summary_invalid")
    manual = package.get("manualAttestations")
    if not isinstance(manual, dict) or manual != MANUAL_EXPECTED:
        blockers.append("manual_attestation_failed")
    no_write = package.get("noWriteAttestations")
    if not isinstance(no_write, dict) or no_write != NO_WRITE_EXPECTED:
        blockers.append("no_write_attestation_failed")

    if blockers:
        return _blocked(*blockers, digest=safe_digest)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "research_only",
        "diagnosticOnly": True,
        "ready": True,
        "contractDigest": safe_digest,
        "blockers": [],
        "limitations": [
            "offline_fixture_validation_only",
            "manual_attestations_not_independently_proven",
            "not_investment_or_pit_evidence",
        ],
    }


def _valid_sql_summary(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != SQL_KEYS:
        return False
    if value.get("schemaVersion") != 1:
        return False
    for name in ("executorRoleIsPostgres", "transactionReadOnly",
                 "pgcryptoNamespaceExact", "digestSignaturePresent"):
        if value.get(name) is not True:
            return False
    if type(value.get("authenticatorOverrideKnown")) is not bool:
        return False
    if value.get("privateSchemaInAuthenticatorOverride") is not False:
        return False
    for name in ("targetRoleCount", "targetSchemaCount", "targetRelationCount",
                 "targetRoutineCount", "privateRuntimeGrantCount",
                 "privateViewExposureCount", "privateRoutineExposureCount",
                 "privatePublicationExposureCount"):
        if type(value.get(name)) is not int or value[name] != 0:
            return False
    return True
