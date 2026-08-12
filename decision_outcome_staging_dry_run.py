"""Build and validate one rollback-only staging exercise for E1C-B1.

This module is deliberately offline.  It accepts already-loaded contract text
and verified fixtures, then returns SQL as a string.  It never connects to a
database, reads credentials, or executes the SQL it produces.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

import decision_outcome_db_contract as db_contract


SCHEMA_VERSION = 1
POLICY_VERSION = "decision-outcome-staging-dry-run-v1"
EXPECTED_ENVIRONMENT = "staging"
ROLE_OWNER = "decision_outcome_owner"
ROLE_WRITER = "decision_outcome_writer"
TARGET_SCHEMA = "investment_decision_shadow_v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _literal(value: str) -> str:
    if "\x00" in value:
        raise ValueError("nul_forbidden")
    return "'" + value.replace("'", "''") + "'"


def _rpc(payload: dict[str, Any]) -> str:
    events = _literal(_canonical(payload["p_events"])) + "::jsonb"
    return (
        "public.append_decision_outcome_snapshot_v1("
        f"{events},{_literal(payload['p_manifest_text'])},"
        f"{_literal(payload['p_manifest_transport_hash'])},"
        f"{_literal(payload['p_anchor_text'])},"
        f"{_literal(payload['p_anchor_transport_hash'])})"
    )


def build(migration_sql: str, events: Any, manifest: Any, anchor: Any,
          *, enabled: bool = False) -> dict[str, Any]:
    """Return a deterministic rollback-only SQL bundle after full revalidation."""
    if not enabled:
        return {"mode": "disabled", "ready": False, "sql": None}
    migration = db_contract.validate_migration(migration_sql, enabled=True)
    mapped = db_contract.serialize(events, manifest, anchor, enabled=True)
    if migration.get("contractReady") is not True or mapped.get("mappingReady") is not True:
        return _blocked("input_contract_not_verified")
    payload = copy.deepcopy(mapped["payload"])
    conflict_anchor = json.loads(payload["p_anchor_text"])
    conflict_anchor["anchorHash"] = "c" * 64
    conflict_text = _canonical(conflict_anchor)
    conflict_payload = copy.deepcopy(payload)
    conflict_payload["p_anchor_text"] = conflict_text
    conflict_payload["p_anchor_transport_hash"] = _hash(conflict_text)
    insert_rpc, conflict_rpc = _rpc(payload), _rpc(conflict_payload)
    sql = f"""-- GENERATED OFFLINE: {POLICY_VERSION}
-- Execute only in the separately verified staging project and only as postgres.
begin;
set local statement_timeout = '30s';
set local lock_timeout = '5s';
set local idle_in_transaction_session_timeout = '30s';

do $identity_guard$
begin
  if current_user <> 'postgres' then
    raise exception 'decision_outcome_staging_executor_invalid';
  end if;
  if pg_catalog.current_setting('transaction_read_only') <> 'off' then
    raise exception 'decision_outcome_dry_run_requires_transactional_ddl';
  end if;
  if pg_catalog.to_regrole('{ROLE_OWNER}') is not null
     or pg_catalog.to_regrole('{ROLE_WRITER}') is not null
     or pg_catalog.to_regnamespace('{TARGET_SCHEMA}') is not null then
    raise exception 'decision_outcome_staging_not_clean';
  end if;
end;
$identity_guard$;

create role {ROLE_OWNER} nologin noinherit nosuperuser nocreatedb
  nocreaterole noreplication nobypassrls;
create role {ROLE_WRITER} nologin noinherit nosuperuser nocreatedb
  nocreaterole noreplication nobypassrls;
-- PostgreSQL 16+ may grant the creating role an implicit membership in each
-- newly created role.  The production contract intentionally requires an
-- empty membership graph, so remove only that creator edge before running the
-- unchanged migration preflight.  This does not grant any privilege.
revoke {ROLE_OWNER} from postgres;
revoke {ROLE_WRITER} from postgres;
revoke create on schema public from {ROLE_OWNER}, {ROLE_WRITER};

{migration_sql.rstrip()}

do $post_migration_guard$
begin
  if pg_catalog.to_regnamespace('{TARGET_SCHEMA}') is null
     or pg_catalog.to_regprocedure('public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)') is null
     or not pg_catalog.has_function_privilege(
       '{ROLE_WRITER}',
       'public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)',
       'EXECUTE'
     )
     or pg_catalog.has_schema_privilege('{ROLE_WRITER}','{TARGET_SCHEMA}','USAGE')
     or pg_catalog.has_table_privilege('{ROLE_WRITER}','{TARGET_SCHEMA}.anchor_v1','INSERT')
     or pg_catalog.has_function_privilege(
       'anon','public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)','EXECUTE'
     )
     or pg_catalog.has_function_privilege(
       'authenticated','public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)','EXECUTE'
     )
     or pg_catalog.has_function_privilege(
       'service_role','public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)','EXECUTE'
     ) then
    raise exception 'decision_outcome_post_migration_privilege_invalid';
  end if;
end;
$post_migration_guard$;

set local role {ROLE_WRITER};
do $direct_dml_denial$
begin
  begin
    execute 'insert into {TARGET_SCHEMA}.anchor_v1 default values';
    raise exception 'decision_outcome_direct_dml_unexpectedly_allowed';
  exception
    when insufficient_privilege then null;
  end;
end;
$direct_dml_denial$;

do $rpc_semantics$
declare
  first_receipt jsonb;
  duplicate_receipt jsonb;
begin
  first_receipt := {insert_rpc};
  if first_receipt->>'status' <> 'inserted'
     or (first_receipt->>'promotionEligible')::boolean
     or not (first_receipt->>'diagnosticOnly')::boolean then
    raise exception 'decision_outcome_insert_receipt_invalid';
  end if;
  duplicate_receipt := {insert_rpc};
  if duplicate_receipt->>'status' <> 'duplicate'
     or duplicate_receipt->>'receiptHash' <> first_receipt->>'receiptHash' then
    raise exception 'decision_outcome_duplicate_receipt_invalid';
  end if;
  begin
    perform {conflict_rpc};
    raise exception 'decision_outcome_conflict_unexpectedly_allowed';
  exception
    when others then
      if sqlerrm <> 'decision_outcome_sequence_conflict' then raise; end if;
  end;
end;
$rpc_semantics$;
reset role;

do $row_guard$
begin
  if (select pg_catalog.count(*) from {TARGET_SCHEMA}.event_blob_v1) <> {len(payload['p_events'])}
     or (select pg_catalog.count(*) from {TARGET_SCHEMA}.manifest_blob_v1) <> 1
     or (select pg_catalog.count(*) from {TARGET_SCHEMA}.anchor_v1) <> 1
     or (select pg_catalog.count(*) from {TARGET_SCHEMA}.audit_v1) <> 2 then
    raise exception 'decision_outcome_dry_run_row_counts_invalid';
  end if;
end;
$row_guard$;

rollback;

begin transaction read only;
select pg_catalog.jsonb_build_object(
  'schemaVersion', {SCHEMA_VERSION},
  'mode', 'research_only',
  'diagnosticOnly', true,
  'policyVersion', '{POLICY_VERSION}',
  'dryRunAssertionsPassed', true,
  'rollbackVerified',
    pg_catalog.to_regrole('{ROLE_OWNER}') is null
    and pg_catalog.to_regrole('{ROLE_WRITER}') is null
    and pg_catalog.to_regnamespace('{TARGET_SCHEMA}') is null
    and pg_catalog.to_regprocedure(
      'public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)'
    ) is null,
  'productionApproved', false
) as decision_outcome_staging_dry_run_summary;
rollback;
"""
    return {
        "mode": "research_only", "diagnosticOnly": True, "ready": True,
        "productionApproved": False, "sql": sql, "sqlHash": _hash(sql),
        "migrationHash": db_contract.PINNED_MIGRATION_SHA256,
        "payloadContractHash": mapped["payloadContractHash"], "blockers": [],
    }


def validate(sql: Any, migration_sql: str, *, enabled: bool = False) -> dict[str, Any]:
    """Static fail-closed validation of a generated dry-run bundle."""
    if not enabled:
        return {"mode": "disabled", "valid": False}
    if not isinstance(sql, str) or not sql.startswith(f"-- GENERATED OFFLINE: {POLICY_VERSION}\n"):
        return _blocked("dry_run_header_invalid") | {"valid": False}
    blockers: list[str] = []
    if migration_sql.rstrip() not in sql:
        blockers.append("pinned_migration_missing")
    required = (
        "begin;", "set local statement_timeout", "set local lock_timeout",
        "decision_outcome_staging_executor_invalid", "decision_outcome_staging_not_clean",
        f"create role {ROLE_OWNER} nologin noinherit nosuperuser",
        f"create role {ROLE_WRITER} nologin noinherit nosuperuser",
        f"revoke {ROLE_OWNER} from postgres;",
        f"revoke {ROLE_WRITER} from postgres;",
        "revoke create on schema public", "set local role decision_outcome_writer",
        "decision_outcome_direct_dml_unexpectedly_allowed", "when insufficient_privilege",
        "decision_outcome_insert_receipt_invalid", "decision_outcome_duplicate_receipt_invalid",
        "decision_outcome_sequence_conflict", "decision_outcome_dry_run_row_counts_invalid",
        "begin transaction read only;", "dryRunAssertionsPassed", "rollbackVerified",
        "'productionApproved', false",
    )
    lowered = sql.lower()
    for fragment in required:
        if fragment.lower() not in lowered:
            blockers.append("required_guard_missing")
    migration_start = lowered.find("do $preflight$")
    for role in (ROLE_OWNER, ROLE_WRITER):
        revoke_at = lowered.find(f"revoke {role} from postgres;")
        if revoke_at < 0 or migration_start < 0 or revoke_at > migration_start:
            blockers.append("creator_membership_guard_order_invalid")
    if sql.rstrip().splitlines()[-1].strip().lower() != "rollback;":
        blockers.append("final_rollback_missing")
    if lowered.count("rollback;") != 2 or lowered.count("begin;") != 1 or lowered.count("begin transaction read only;") != 1:
        blockers.append("transaction_boundary_invalid")
    forbidden = (
        "supabase_url", "service_role_key", "postgresql://", "https://",
        "telegram", "strategy_tracker", "promotion_status", "investment_advice",
        "commit;", "create extension", "alter publication", "create publication",
    )
    if any(item in lowered for item in forbidden):
        blockers.append("external_or_formal_operation_forbidden")
    return {
        "mode": "research_only", "diagnosticOnly": True,
        "valid": not blockers, "productionApproved": False,
        "sqlHash": _hash(sql), "blockers": sorted(set(blockers)),
    }


def _blocked(*codes: str) -> dict[str, Any]:
    return {
        "mode": "research_only", "diagnosticOnly": True, "ready": False,
        "productionApproved": False, "sql": None, "blockers": sorted(set(codes)),
    }
