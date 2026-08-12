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
MIGRATION_CONTRACT_VERSION = 2
MIGRATION_FILE = "supabase-announcement-first-seen-shadow-v1.sql"
PINNED_MIGRATION_SHA256 = "d85f00aad322e59d79621db1434098713bbaf4b7be9c828d530f10d04f84505f"
PINNED_SEMANTIC_CONTRACT_SHA256 = "0b52443aea14471851775b8da705cdb332319aa1daa18c2b6916209b5d79132c"
TABLE = "investment_announcement_first_seen_shadow_v1"
AUDIT_TABLE = "investment_announcement_first_seen_audit_v1"
ADMIN_SCHEMA = "investment_lineage_admin_v1"
LEDGER_TABLE = "contract_migration_ledger_v1"
RPC = "append_announcement_first_seen_shadow_v1"
MUTATION_TRIGGER = "reject_announcement_first_seen_mutation_v1"
MIGRATION_ID = "announcement-first-seen-shadow-v1-b2a2"
ROLE_ATTRIBUTES = (
    "NOLOGIN", "NOINHERIT", "NOSUPERUSER", "NOBYPASSRLS",
    "NOCREATEDB", "NOCREATEROLE", "NOREPLICATION",
)

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

SEMANTIC_MANIFEST = {
    "migrationId": MIGRATION_ID,
    "migrationContractVersion": MIGRATION_CONTRACT_VERSION,
    "schemaVersion": SCHEMA_VERSION,
    "adminSchema": ADMIN_SCHEMA,
    "ledger": LEDGER_TABLE,
    "roles": {
        "owner": {"name": "lineage_observer_owner", "attributes": list(ROLE_ATTRIBUTES)},
        "writer": {"name": "lineage_observer_writer", "attributes": list(ROLE_ATTRIBUTES)},
    },
    "extension": "extensions.digest(bytea,text)",
    "definerSearchPath": "pg_catalog",
    "tables": {
        AUDIT_TABLE: {"forceRls": True, "ownerPrivileges": ["INSERT", "SEQUENCE_USAGE"],
                      "policies": ["owner_insert"]},
        TABLE: {"forceRls": True, "ownerPrivileges": ["SELECT", "INSERT"],
                "policies": ["owner_select", "owner_insert"]},
        f"{ADMIN_SCHEMA}.{LEDGER_TABLE}": {"privateUnexposed": True,
                                            "runtimePrivileges": [], "policies": []},
    },
    "triggers": ["main_update_delete_reject", "audit_update_delete_reject",
                 "ledger_update_delete_reject"],
    "writer": {"functionPrivileges": ["append:EXECUTE"],
               "schemaPrivileges": ["public:USAGE"], "tablePrivileges": []},
    "deniedRoles": ["PUBLIC", "anon", "authenticated", "service_role"],
}


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
    """Validate the pinned B2A2 SQL fixture without reading or executing a DB."""
    if not enabled:
        return {"mode": "disabled", "contractReady": False, "migrationExecuted": False}
    blockers: list[str] = []
    digest = _hash(sql)
    if digest != PINNED_MIGRATION_SHA256:
        blockers.append("migration_hash_unpinned_or_drifted")
    lowered = sql.lower()
    semantic_hash = _canonical_hash(SEMANTIC_MANIFEST)
    if semantic_hash != PINNED_SEMANTIC_CONTRACT_SHA256:
        blockers.append("semantic_manifest_hash_drifted")
    if PINNED_SEMANTIC_CONTRACT_SHA256 not in lowered:
        blockers.append("semantic_manifest_pin_missing")
    if "available_at" in lowered or "published_at" in lowered:
        blockers.append("historical_availability_alias_forbidden")
    destructive = re.findall(r"(?mi)^\s*(drop|truncate|update|delete)\b", sql)
    if destructive:
        blockers.append("destructive_statement_forbidden")
    forbidden_ddl = ("create table if not exists", "create or replace", "start transaction", "commit;", "rollback;")
    if any(fragment in lowered for fragment in forbidden_ddl):
        blockers.append("rerun_or_transaction_control_forbidden")
    created_tables = set(re.findall(
        r"(?i)create table (?:if not exists )?([a-z0-9_]+)\.([a-z0-9_]+)", sql
    ))
    expected_tables = {("public", TABLE), ("public", AUDIT_TABLE), (ADMIN_SCHEMA, LEDGER_TABLE)}
    if created_tables != expected_tables:
        blockers.append("foreign_or_missing_table_change")
    inserted_tables = set(re.findall(r"(?i)insert into ([a-z0-9_]+)\.([a-z0-9_]+)", sql))
    if inserted_tables != expected_tables:
        blockers.append("foreign_or_missing_insert_target")
    required = (
        "current_user <> 'postgres'", "b2a2_requires_postgres_migration_role",
        "pgcrypto' and n.nspname = 'extensions'", "extensions.digest(bytea,text)",
        "lineage_observer_owner','lineage_observer_writer", "rolcanlogin", "rolinherit",
        "rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "rolreplication",
        "pg_auth_members", "has_schema_privilege(role_name, 'public', 'create')",
        "b2a2_migration_target_exists", f"create schema {ADMIN_SCHEMA} authorization postgres",
        f"revoke all on schema {ADMIN_SCHEMA}", f"revoke all on {ADMIN_SCHEMA}.{LEDGER_TABLE}",
        "announcement-first-seen-shadow-v1-b2a2', 2, 1",
        f"alter table public.{TABLE} enable row level security",
        f"alter table public.{TABLE} force row level security",
        f"alter table public.{AUDIT_TABLE} enable row level security",
        f"alter table public.{AUDIT_TABLE} force row level security",
        f"grant select, insert on public.{TABLE} to lineage_observer_owner",
        f"grant insert on public.{AUDIT_TABLE} to lineage_observer_owner",
        f"grant usage on sequence public.{AUDIT_TABLE}_audit_id_seq to lineage_observer_owner",
        "create policy announcement_first_seen_owner_select_v1",
        "for select to lineage_observer_owner using (true)",
        "create policy announcement_first_seen_owner_insert_v1",
        "create policy announcement_first_seen_audit_owner_insert_v1",
        "for insert to lineage_observer_owner with check",
        f"before update or delete on public.{TABLE}",
        f"before update or delete on public.{AUDIT_TABLE}",
        f"before update or delete on {ADMIN_SCHEMA}.{LEDGER_TABLE}",
        "security definer", "set search_path = pg_catalog",
        "grant usage on schema extensions to lineage_observer_owner",
        "grant execute on function extensions.digest(bytea,text) to lineage_observer_owner",
        "extensions.digest(pg_catalog.convert_to",
        "revoke all on function public.reject_announcement_first_seen_mutation_v1()",
        f"grant usage on schema public to lineage_observer_writer",
        f"grant execute on function public.{RPC}", "to lineage_observer_writer",
        "from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer",
        "forward_observed_only", "private_lineage", "pg_advisory_xact_lock",
        "announcement_first_seen_identity_conflict", "announcement_first_seen_correction_conflict",
        "announcement_first_seen_record_hash_invalid",
    )
    for fragment in required:
        if fragment not in lowered:
            blockers.append("required_guard_missing:" + fragment.replace(" ", "_"))
    if re.search(r"(?i)grant\s+.*\s+to\s+(?:public|anon|authenticated|service_role)\b", sql):
        blockers.append("denied_role_grant_forbidden")
    if re.search(rf"(?i)grant\s+(?:select|insert|update|delete|truncate|references|trigger|all).*\s+to\s+lineage_observer_writer\b", sql):
        blockers.append("direct_table_privilege_grant_forbidden")
    if re.search(r"(?i)grant\s+create\s+on\s+schema", sql):
        blockers.append("schema_create_grant_forbidden")
    if (re.search(r"(?i)create\s+(?:materialized\s+)?view\b", sql)
            or "storage." in lowered
            or re.search(rf"(?i)create\s+function\s+{ADMIN_SCHEMA}\.", sql)
            or re.search(rf"(?i)grant\s+.*\s+on\s+.*{ADMIN_SCHEMA}", sql)):
        blockers.append("private_ledger_exposure_forbidden")
    if lowered.count(f"{ADMIN_SCHEMA}.{LEDGER_TABLE}") < 4:
        blockers.append("private_ledger_isolation_incomplete")
    ready = not blockers
    return {
        "mode": "research_only", "diagnosticOnly": True,
        "contractReady": ready, "migrationExecuted": False, "serviceRoleSafe": False,
        "migrationHash": digest, "expectedMigrationHash": PINNED_MIGRATION_SHA256,
        "semanticContractHash": semantic_hash,
        "expectedSemanticContractHash": PINNED_SEMANTIC_CONTRACT_SHA256,
        "tableColumns": sorted(SERVER_COLUMNS), "serializerColumns": list(ROW_COLUMNS),
        "blockers": sorted(set(blockers)) + ["service_role_bypass_not_approved", "migration_not_executed"],
        "limitations": ["offline_static_validation_only", "no_database_introspection",
                        "no_rpc_invocation", "dedicated_roles_not_yet_verified",
                        "private_schema_exposure_not_runtime_verified"],
    }


def privilege_model(*, enabled: bool = False) -> dict[str, Any]:
    """Pinned offline privilege model; it does not inspect or alter a database."""
    if not enabled:
        return {"mode": "disabled"}
    denied = {operation: False for operation in ("select", "insert", "update", "delete", "truncate")}
    return {
        "mode": "research_only", "diagnosticOnly": True,
        "directTablePrivileges": {
            "public": copy.deepcopy(denied), "anon": copy.deepcopy(denied),
            "authenticated": copy.deepcopy(denied),
            "lineage_observer_writer": copy.deepcopy(denied),
        },
        "rpcExecute": {"lineage_observer_writer": True, "public": False,
                       "anon": False, "authenticated": False, "service_role": False},
        "ownerPrivileges": {"main": ["select", "insert"], "audit": ["insert"],
                             "auditSequence": ["usage"]},
        "ownerPolicies": {"main": ["select", "insert"], "audit": ["insert"],
                          "update": [], "delete": []},
        "writerSchemaPrivileges": {"public": ["usage"], ADMIN_SCHEMA: []},
        "privateLedger": {"schema": ADMIN_SCHEMA, "privateUnexposed": True,
                          "runtimePrivileges": [], "dataApiExposure": False},
        "roleRequirements": {"lineage_observer_owner": list(ROLE_ATTRIBUTES),
                             "lineage_observer_writer": list(ROLE_ATTRIBUTES)},
        "forceRls": True, "updateDeleteTriggerAllCallers": True,
        "serviceRoleBypassesRls": True, "serviceRoleSafeForRoutineProducer": False,
        "blockers": ["service_role_bypass_not_approved", "dedicated_roles_not_yet_verified"],
    }


def migration_preflight(existing_targets: set[str] | None = None, *, enabled: bool = False) -> dict[str, Any]:
    """Model strict single-use behavior; it never inspects a real database."""
    if not enabled:
        return {"mode": "disabled", "ready": False}
    targets = set(existing_targets or ())
    if targets:
        return {"mode": "research_only", "diagnosticOnly": True, "ready": False,
                "reason": "migration_target_exists", "targets": sorted(targets)}
    return {"mode": "research_only", "diagnosticOnly": True, "ready": True,
            "migrationId": MIGRATION_ID, "singleUse": True}


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
