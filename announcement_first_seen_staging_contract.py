"""Pure, offline validators for the B2B1 staging contract fixtures."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import announcement_first_seen_db_contract as db

VALIDATOR_SQL_FILE = "supabase-announcement-first-seen-validate-v1.sql"
PREFLIGHT_SQL_FILE = "supabase-announcement-first-seen-staging-preflight-v1.sql"
PINNED_VALIDATOR_SHA256 = "4d0bcb6e524cf790368265a1dfa5ce8870304399a87314b73b1ad4629b6a844e"
PINNED_PREFLIGHT_SHA256 = "7377ac324c589bd1ae7504bf3f99c0648c519d5e1e5febca6e9a46d3fd757779"
MAIN_SHA = "7cc49f8608bc6c8c1604768bea30f0900a536519"
VALIDATOR_RPC = "validate_announcement_first_seen_payload_v1"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _delimited_hash(values: list[Any]) -> str:
    return _hash("\x1f".join("" if value is None else str(value) for value in values))


def _utc_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _blocked(*reasons: str) -> dict[str, Any]:
    return {"mode": "research_only", "diagnosticOnly": True,
            "valid": False, "blockers": sorted(set(reasons))}


def validate_payload(payload: Any, *, enabled: bool = False) -> dict[str, Any]:
    """Recompute canonical hashes without I/O or echoing sensitive payload values."""
    if not enabled:
        return {"mode": "disabled", "valid": False}
    if not isinstance(payload, dict) or tuple(payload) != db.ROW_COLUMNS:
        return _blocked("payload_contract_invalid")
    if any(key in payload for key in ("availableAt", "available_at", "publishedAt", "published_at")):
        return _blocked("historical_availability_alias_forbidden")
    fixed = (
        payload.get("provider") in ("TWSE", "TPEX"),
        payload.get("market") in ("listed", "otc", "emerging"),
        payload.get("event_type") in ("listing", "delisting"),
        payload.get("schema_version") == 1,
        payload.get("policy_version") == db.POLICY_VERSION,
        payload.get("evidence_mode") == "forward_observed_only",
        payload.get("visibility") == "private_lineage",
        payload.get("metadata") == db.METADATA,
        _utc_text(payload.get("first_seen_at")) is not None,
    )
    if not all(fixed) or not all(HASH_RE.fullmatch(str(payload.get(k, ""))) for k in
                                 ("content_hash", "composite_key", "record_hash")):
        return _blocked("payload_contract_invalid")
    supersedes = payload.get("supersedes_content_hash")
    if supersedes is not None and not HASH_RE.fullmatch(str(supersedes)):
        return _blocked("payload_contract_invalid")
    composite = _delimited_hash([
        payload["provider"], payload["source_contract_id"], payload["official_document_id"],
        payload["entity_id"], payload["event_type"], payload["effective_date"],
        payload["source_revision"], payload["schema_version"],
    ])
    if composite != payload["composite_key"]:
        return _blocked("composite_hash_invalid")
    record_hash = _delimited_hash([
        payload["provider"], payload["market"], payload["source_contract_id"],
        payload["official_document_id"], payload["official_letter_no"], payload["entity_id"],
        payload["event_type"], payload["effective_date"], payload["source_revision"],
        payload["content_hash"], payload["first_seen_at"], supersedes,
        payload["composite_key"], payload["schema_version"], payload["policy_version"],
        payload["evidence_mode"], payload["visibility"],
    ])
    if record_hash != payload["record_hash"]:
        return _blocked("record_hash_invalid")
    return {"mode": "research_only", "diagnosticOnly": True, "valid": True,
            "status": "valid", "recordHash": record_hash, "blockers": []}


def validate_validator_sql(sql: str, *, enabled: bool = False) -> dict[str, Any]:
    """Statically verify the pure no-write/no-read SECURITY DEFINER validator."""
    if not enabled:
        return {"mode": "disabled", "contractReady": False, "migrationExecuted": False}
    blockers: list[str] = []
    lowered = sql.lower()
    if _hash(sql) != PINNED_VALIDATOR_SHA256:
        blockers.append("validator_hash_unpinned_or_drifted")
    required = (
        f"create function public.{VALIDATOR_RPC}", "returns jsonb", "language plpgsql",
        "security definer", "set search_path = pg_catalog", "extensions.digest(",
        "announcement_first_seen_validation_contract_invalid",
        "announcement_first_seen_validation_composite_invalid",
        "announcement_first_seen_validation_record_invalid",
        "from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer",
        f"alter function public.{VALIDATOR_RPC}", "owner to lineage_observer_owner",
        f"grant execute on function public.{VALIDATOR_RPC}", "to lineage_observer_writer",
    )
    for item in required:
        if item not in lowered:
            blockers.append("required_guard_missing")
    match = re.search(r"(?is)as\s+\$\$(.*?)\$\$;", sql)
    body = match.group(1).lower() if match else ""
    forbidden_body = (
        r"\bselect\b", r"\bfrom\b", r"\binsert\b", r"\bupdate\b", r"\bdelete\b",
        r"\btruncate\b", r"\bmerge\b", r"\bcopy\b", r"\bperform\b",
        r"current_user", r"session_user", r"current_setting", r"\bauth\.",
        r"\bnow\s*\(", r"clock_timestamp", r"statement_timestamp", r"transaction_timestamp",
        r"investment_announcement", r"contract_migration_ledger", r"investment_lineage_admin",
    )
    if not body or any(re.search(pattern, body) for pattern in forbidden_body):
        blockers.append("validator_not_relation_free")
    if any(alias in lowered for alias in ("available_at", "published_at", "availableat", "publishedat")):
        blockers.append("historical_availability_alias_forbidden")
    if re.search(r"(?i)grant\s+execute.*\bto\s+(?:public|anon|authenticated|service_role)\b", sql):
        blockers.append("validator_acl_too_broad")
    return {"mode": "research_only", "diagnosticOnly": True,
            "contractReady": not blockers, "migrationExecuted": False,
            "validatorHash": _hash(sql), "blockers": sorted(set(blockers))}


def validate_preflight_sql(sql: str, *, enabled: bool = False) -> dict[str, Any]:
    """Validate a pinned catalog-only, read-only staging introspection fixture."""
    if not enabled:
        return {"mode": "disabled", "contractReady": False, "executed": False}
    blockers: list[str] = []
    lowered = sql.lower()
    if _hash(sql) != PINNED_PREFLIGHT_SHA256:
        blockers.append("preflight_hash_unpinned_or_drifted")
    if "begin transaction read only;" not in lowered or "commit;" not in lowered:
        blockers.append("read_only_transaction_missing")
    required = ("pgcryptoNamespaceExact", "digestSignaturePresent", "targetRoleCount",
                "targetSchemaCount", "targetRelationCount", "targetRoutineCount",
                "privateRuntimeGrantCount", "privateViewExposureCount",
                "privateRoutineExposureCount", "privatePublicationExposureCount")
    for item in required:
        if item.lower() not in lowered:
            blockers.append("required_preflight_field_missing")
    forbidden = (r"\binsert\b", r"\bupdate\b", r"\bdelete\b", r"\btruncate\b", r"\bmerge\b",
                 r"\bcopy\b", r"\bcreate\b", r"\balter\b", r"\bdrop\b", r"\bgrant\b", r"\brevoke\b",
                 r"\bnow\s*\(", r"clock_timestamp", r"statement_timestamp", r"transaction_timestamp",
                 r"https?://", r"\btoken\b", r"\bsecret\b")
    executable = "\n".join(line for line in lowered.splitlines() if not line.lstrip().startswith("--"))
    if any(re.search(pattern, executable) for pattern in forbidden):
        blockers.append("preflight_unsafe_operation")
    allowed_relations = ("pg_catalog.", "information_schema.usage_privileges")
    for relation in re.findall(r"(?i)\b(?:from|join)\s+([a-z0-9_.]+)", executable):
        if not relation.startswith(allowed_relations):
            blockers.append("preflight_non_catalog_relation")
    if re.search(r"(?i)from\s+(?:public|investment_lineage_admin_v1)\.", executable):
        blockers.append("private_or_application_row_read_forbidden")
    return {"mode": "research_only", "diagnosticOnly": True,
            "contractReady": not blockers, "executed": False,
            "preflightHash": _hash(sql), "blockers": sorted(set(blockers))}


def evaluate_preflight(summary: Any, *, enabled: bool = False) -> dict[str, Any]:
    """Fail-closed evaluation of manually supplied, non-sensitive staging metadata."""
    if not enabled:
        return {"mode": "disabled", "ready": False}
    allowed = {
        "schemaVersion", "environment", "stagingRefHash", "productionRefHash", "mainSha",
        "b2a2SqlPin", "b2a2SemanticPin", "executorRoleIsPostgres", "transactionReadOnly",
        "pgcryptoNamespaceExact", "digestSignaturePresent", "targetRoleCount",
        "targetSchemaCount", "targetRelationCount", "targetRoutineCount",
        "dashboardExposureVerified", "privateSchemaInDashboardExposed",
        "authenticatorOverrideAbsentOrSafe", "privateRuntimeGrantCount",
        "privateViewExposureCount", "privateRoutineExposureCount",
        "privatePublicationExposureCount", "formalIsolationVerified",
    }
    if not isinstance(summary, dict) or set(summary) != allowed:
        return _blocked("preflight_summary_contract_invalid") | {"ready": False}
    blockers: list[str] = []
    if summary["schemaVersion"] != 1 or summary["environment"] != "staging":
        blockers.append("staging_identity_invalid")
    refs = (summary["stagingRefHash"], summary["productionRefHash"])
    if not all(isinstance(v, str) and HASH_RE.fullmatch(v) for v in refs) or refs[0] == refs[1]:
        blockers.append("project_separation_unverified")
    if summary["mainSha"] != MAIN_SHA or summary["b2a2SqlPin"] != db.PINNED_MIGRATION_SHA256 or summary["b2a2SemanticPin"] != db.PINNED_SEMANTIC_CONTRACT_SHA256:
        blockers.append("contract_pin_mismatch")
    for name in ("executorRoleIsPostgres", "transactionReadOnly", "pgcryptoNamespaceExact",
                 "digestSignaturePresent", "dashboardExposureVerified",
                 "authenticatorOverrideAbsentOrSafe", "formalIsolationVerified"):
        if summary[name] is not True:
            blockers.append("required_preflight_assertion_failed")
    if summary["privateSchemaInDashboardExposed"] is not False:
        blockers.append("private_schema_exposure")
    if any(summary[name] != 0 for name in ("targetRoleCount", "targetSchemaCount",
                                           "targetRelationCount", "targetRoutineCount",
                                           "privateRuntimeGrantCount", "privateViewExposureCount",
                                           "privateRoutineExposureCount", "privatePublicationExposureCount")):
        blockers.append("staging_not_clean_or_private")
    digest = _hash(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return {"mode": "research_only", "diagnosticOnly": True, "ready": not blockers,
            "contractDigest": digest, "blockers": sorted(set(blockers)),
            "limitations": ["offline_metadata_only", "no_database_connection", "no_migration_execution"]}
