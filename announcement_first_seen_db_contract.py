"""Offline serializer and static validator for the unexecuted first-seen DB v1 contract."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
POLICY_VERSION = "official-announcement-first-seen-v1"
MIGRATION_FILE = "supabase-announcement-first-seen-shadow-v1.sql"
PINNED_MIGRATION_SHA256 = "c62abe1cf9dde1c7b9685631ec7d5dc71dda974893ecec1d0e8af200b3237775"
TABLE = "investment_announcement_first_seen_shadow_v1"
AUDIT_TABLE = "investment_announcement_first_seen_audit_v1"
RPC = "append_announcement_first_seen_shadow_v1"
MUTATION_TRIGGER = "reject_announcement_first_seen_mutation_v1"

ROW_COLUMNS = (
    "provider", "market", "source_contract_id", "official_document_id",
    "official_letter_no", "entity_id", "event_type", "effective_date",
    "source_revision", "content_hash", "first_seen_at", "supersedes_content_hash",
    "composite_key", "record_hash", "schema_version", "policy_version",
    "evidence_mode", "visibility", "metadata",
)
SERVER_COLUMNS = set(ROW_COLUMNS) | {"created_at"}
METADATA = {
    "classification": "FORWARD_OBSERVED_ONLY",
    "limitations": ["forward_only", "no_historical_backfill", "not_formal_advice_evidence"],
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


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


def _source_record_valid(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("mode") != "research_only" or record.get("diagnosticOnly") is not True:
        return False
    if record.get("classification") != "FORWARD_OBSERVED_ONLY" or record.get("historicalEligible") is not False:
        return False
    if record.get("visibility") != "private_lineage" or record.get("schemaVersion") != 1:
        return False
    if record.get("policyVersion") != POLICY_VERSION or _utc_text(record.get("firstSeenAtUtc")) is None:
        return False
    if any(key in record for key in ("availableAt", "available_at", "publishedAt", "published_at")):
        return False
    if not HASH_RE.fullmatch(str(record.get("contentHash", ""))):
        return False
    payload = copy.deepcopy(record)
    stored_hash = payload.pop("recordHash", None)
    if not isinstance(stored_hash, str) or _canonical_hash(payload) != stored_hash:
        return False
    identity = {
        "provider": record.get("provider"), "sourceContractId": record.get("sourceContractId"),
        "officialDocumentId": record.get("officialDocumentId"), "entityId": record.get("entityId"),
        "eventType": record.get("eventType"), "effectiveDate": record.get("effectiveDate"),
        "sourceRevision": record.get("sourceRevision"), "schemaVersion": record.get("schemaVersion"),
    }
    return record.get("compositeKey") == _canonical_hash(identity)


def serialize(record: dict[str, Any], *, enabled: bool = False) -> dict[str, Any]:
    """Map one B1 record to the isolated snake_case contract; never performs I/O."""
    if not enabled:
        return {"mode": "disabled", "row": None, "mappingReady": False}
    if not _source_record_valid(record):
        return _blocked("source_record_contract_invalid")
    first_seen = _utc_text(record["firstSeenAtUtc"])
    composite = _delimited_hash([
        record["provider"], record["sourceContractId"], record["officialDocumentId"],
        record["entityId"], record["eventType"], record["effectiveDate"],
        record["sourceRevision"], SCHEMA_VERSION,
    ])
    row = {
        "provider": record["provider"], "market": record["market"],
        "source_contract_id": record["sourceContractId"],
        "official_document_id": record["officialDocumentId"],
        "official_letter_no": record["officialLetterNo"], "entity_id": record["entityId"],
        "event_type": record["eventType"], "effective_date": record["effectiveDate"],
        "source_revision": record["sourceRevision"], "content_hash": record["contentHash"],
        "first_seen_at": first_seen, "supersedes_content_hash": record.get("supersedesContentHash"),
        "composite_key": composite, "record_hash": "", "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION, "evidence_mode": "forward_observed_only",
        "visibility": "private_lineage", "metadata": copy.deepcopy(METADATA),
    }
    row["record_hash"] = _delimited_hash([
        row["provider"], row["market"], row["source_contract_id"], row["official_document_id"],
        row["official_letter_no"], row["entity_id"], row["event_type"], row["effective_date"],
        row["source_revision"], row["content_hash"], row["first_seen_at"],
        row["supersedes_content_hash"], row["composite_key"], row["schema_version"],
        row["policy_version"], row["evidence_mode"], row["visibility"],
    ])
    if tuple(row) != ROW_COLUMNS:
        return _blocked("serializer_column_order_drift")
    return {"mode": "research_only", "diagnosticOnly": True,
            "mappingReady": True, "blockers": [], "row": row,
            "rowContractHash": _canonical_hash({"columns": ROW_COLUMNS, "schema": SCHEMA_VERSION})}


def validate_migration(sql: str, *, enabled: bool = False) -> dict[str, Any]:
    """Validate a supplied SQL fixture. It never reads or executes the migration."""
    if not enabled:
        return {"mode": "disabled", "contractReady": False, "migrationExecuted": False}
    blockers: list[str] = []
    digest = _hash(sql)
    if digest != PINNED_MIGRATION_SHA256:
        blockers.append("migration_hash_unpinned_or_drifted")
    lowered = sql.lower()
    if "available_at" in lowered or "published_at" in lowered:
        blockers.append("historical_availability_alias_forbidden")
    destructive = re.findall(r"(?mi)^\s*(drop|truncate|update|delete)\b", sql)
    if destructive:
        blockers.append("destructive_statement_forbidden")
    created_tables = set(re.findall(r"(?i)create table if not exists public\.([a-z0-9_]+)", sql))
    if created_tables != {TABLE, AUDIT_TABLE}:
        blockers.append("foreign_or_missing_table_change")
    inserted_tables = set(re.findall(r"(?i)insert into public\.([a-z0-9_]+)", sql))
    if not inserted_tables or not inserted_tables.issubset({TABLE, AUDIT_TABLE}):
        blockers.append("foreign_or_missing_insert_target")
    required = (
        f"alter table public.{TABLE} enable row level security",
        f"alter table public.{TABLE} force row level security",
        f"alter table public.{AUDIT_TABLE} enable row level security",
        f"alter table public.{AUDIT_TABLE} force row level security",
        f"before update or delete on public.{TABLE}",
        f"before update or delete on public.{AUDIT_TABLE}",
        "security definer", "set search_path = pg_catalog, public",
        f"grant execute on function public.{RPC}", "to lineage_observer_writer",
        "from public, anon, authenticated, service_role, lineage_observer_writer",
        "from public, anon, authenticated, service_role",
        "forward_observed_only", "private_lineage", "pg_advisory_xact_lock",
        "announcement_first_seen_identity_conflict", "announcement_first_seen_correction_conflict",
        "announcement_first_seen_record_hash_invalid",
    )
    for fragment in required:
        if fragment not in lowered:
            blockers.append("required_guard_missing:" + fragment.replace(" ", "_"))
    if re.search(r"(?i)grant\s+(?:select|insert|update|delete|all).*\b(?:public|anon|authenticated|service_role|lineage_observer_writer)\b", sql):
        blockers.append("direct_table_privilege_grant_forbidden")
    ready = not blockers
    return {
        "mode": "research_only", "diagnosticOnly": True,
        "contractReady": ready, "migrationExecuted": False, "serviceRoleSafe": False,
        "migrationHash": digest, "expectedMigrationHash": PINNED_MIGRATION_SHA256,
        "tableColumns": sorted(SERVER_COLUMNS), "serializerColumns": list(ROW_COLUMNS),
        "blockers": sorted(set(blockers)) + ["service_role_bypass_not_approved", "migration_not_executed"],
        "limitations": ["offline_static_validation_only", "no_database_introspection",
                        "no_rpc_invocation", "dedicated_roles_not_yet_verified"],
    }


def privilege_model(*, enabled: bool = False) -> dict[str, Any]:
    """Pinned offline privilege model; it does not inspect or alter a database."""
    if not enabled:
        return {"mode": "disabled"}
    denied = {operation: False for operation in ("select", "insert", "update", "delete")}
    return {
        "mode": "research_only", "diagnosticOnly": True,
        "directTablePrivileges": {
            "public": copy.deepcopy(denied), "anon": copy.deepcopy(denied),
            "authenticated": copy.deepcopy(denied),
            "lineage_observer_writer": copy.deepcopy(denied),
        },
        "rpcExecute": {"lineage_observer_writer": True, "public": False,
                       "anon": False, "authenticated": False, "service_role": False},
        "forceRls": True, "updateDeleteTriggerAllCallers": True,
        "serviceRoleBypassesRls": True, "serviceRoleSafeForRoutineProducer": False,
        "blockers": ["service_role_bypass_not_approved", "dedicated_roles_not_yet_verified"],
    }


class AppendRpcModel:
    """In-memory model of the proposed atomic insert RPC; never writes externally."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._heads: dict[tuple[str, str, str], str] = {}

    def append(self, row: dict[str, Any]) -> dict[str, str]:
        if not _valid_row(row):
            return {"status": "blocked", "reason": "row_contract_invalid"}
        key = row["composite_key"]
        existing = self._rows.get(key)
        if existing is not None:
            if existing == row:
                return {"status": "duplicate"}
            return {"status": "conflict", "reason": "identity_conflict"}
        head_key = (row["provider"], row["official_document_id"], row["entity_id"])
        head = self._heads.get(head_key)
        supersedes = row.get("supersedes_content_hash")
        if head is not None and supersedes != head:
            return {"status": "conflict", "reason": "correction_conflict"}
        if head is None and supersedes is not None:
            return {"status": "conflict", "reason": "unknown_supersedes"}
        self._rows[key] = copy.deepcopy(row)
        self._heads[head_key] = row["content_hash"]
        return {"status": "inserted"}


def _valid_row(row: Any) -> bool:
    if not isinstance(row, dict) or tuple(row) != ROW_COLUMNS:
        return False
    if row.get("evidence_mode") != "forward_observed_only" or row.get("visibility") != "private_lineage":
        return False
    if row.get("metadata") != METADATA or _utc_text(row.get("first_seen_at")) is None:
        return False
    if any(alias in row for alias in ("available_at", "published_at")):
        return False
    expected_composite = _delimited_hash([
        row["provider"], row["source_contract_id"], row["official_document_id"],
        row["entity_id"], row["event_type"], row["effective_date"],
        row["source_revision"], row["schema_version"],
    ])
    if row.get("composite_key") != expected_composite:
        return False
    expected_record = _delimited_hash([
        row["provider"], row["market"], row["source_contract_id"], row["official_document_id"],
        row["official_letter_no"], row["entity_id"], row["event_type"], row["effective_date"],
        row["source_revision"], row["content_hash"], row["first_seen_at"],
        row["supersedes_content_hash"], row["composite_key"], row["schema_version"],
        row["policy_version"], row["evidence_mode"], row["visibility"],
    ])
    return row.get("record_hash") == expected_record


def _blocked(*blockers: str) -> dict[str, Any]:
    return {"mode": "research_only", "diagnosticOnly": True,
            "mappingReady": False, "blockers": sorted(set(blockers)), "row": None}
