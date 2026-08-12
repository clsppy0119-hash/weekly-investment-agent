"""Build one bounded, rollback-only staging role-membership diagnostic.

The probe intentionally raises a final exception containing a small sanitized
catalog summary.  That makes the database request fail closed and roll back the
temporary role DDL even when a SQL client stops after the exception.  It never
runs the migration, creates schemas/tables, invokes RPCs, or reads app rows.
"""
from __future__ import annotations

import hashlib
from typing import Any


SCHEMA_VERSION = 1
POLICY_VERSION = "decision-outcome-membership-probe-v1"
ROLE_OWNER = "decision_outcome_owner"
ROLE_WRITER = "decision_outcome_writer"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build(*, enabled: bool = False) -> dict[str, Any]:
    """Return fixed SQL; disabled mode emits no SQL and has no side effects."""
    if not enabled:
        return {"schemaVersion": SCHEMA_VERSION, "mode": "disabled", "ready": False,
                "sql": None, "sqlHash": None, "blockers": ["feature_disabled"]}
    sql = f"""-- GENERATED OFFLINE: {POLICY_VERSION}
-- Run once only in the separately verified staging project as postgres.
begin;
set local statement_timeout = '15s';
set local lock_timeout = '5s';
set local idle_in_transaction_session_timeout = '15s';

do $membership_probe$
declare
  membership_summary jsonb;
begin
  if current_user <> 'postgres' then
    raise exception 'membership_probe_executor_invalid';
  end if;
  if pg_catalog.to_regrole('{ROLE_OWNER}') is not null
     or pg_catalog.to_regrole('{ROLE_WRITER}') is not null then
    raise exception 'membership_probe_staging_not_clean';
  end if;

  create role {ROLE_OWNER} nologin noinherit nosuperuser nocreatedb
    nocreaterole noreplication nobypassrls;
  create role {ROLE_WRITER} nologin noinherit nosuperuser nocreatedb
    nocreaterole noreplication nobypassrls;
  revoke {ROLE_OWNER} from postgres;
  revoke {ROLE_WRITER} from postgres;

  select pg_catalog.coalesce(
    pg_catalog.jsonb_agg(
      pg_catalog.jsonb_build_object(
        'grantedRole', granted_role.rolname,
        'memberRole', member_role.rolname,
        'grantorRole', grantor_role.rolname,
        'options', pg_catalog.to_jsonb(m)
          - 'roleid' - 'member' - 'grantor'
      ) order by granted_role.rolname, member_role.rolname, grantor_role.rolname
    ), '[]'::jsonb
  ) into membership_summary
  from pg_catalog.pg_auth_members m
  join pg_catalog.pg_roles granted_role on granted_role.oid = m.roleid
  join pg_catalog.pg_roles member_role on member_role.oid = m.member
  join pg_catalog.pg_roles grantor_role on grantor_role.oid = m.grantor
  where granted_role.rolname in ('{ROLE_OWNER}','{ROLE_WRITER}')
     or member_role.rolname in ('{ROLE_OWNER}','{ROLE_WRITER}');

  -- The exception is intentional: it is the diagnostic output and guarantees
  -- the request is rolled back even when the client stops on an error.
  raise exception 'membership_probe_result:%', membership_summary;
end;
$membership_probe$;

rollback;
"""
    checked = validate(sql, enabled=True)
    if not checked["valid"]:
        return {"schemaVersion": SCHEMA_VERSION, "mode": "research_only", "ready": False,
                "sql": None, "sqlHash": None, "blockers": checked["blockers"]}
    return {"schemaVersion": SCHEMA_VERSION, "mode": "research_only", "ready": True,
            "sql": sql, "sqlHash": _hash(sql), "blockers": [],
            "expectedResult": "intentional_exception_then_zero_residue_check"}


def validate(sql: Any, *, enabled: bool = False) -> dict[str, Any]:
    if not enabled:
        return {"schemaVersion": SCHEMA_VERSION, "mode": "disabled", "valid": False,
                "sqlHash": None, "blockers": ["feature_disabled"]}
    blockers: list[str] = []
    if not isinstance(sql, str) or not sql.startswith(f"-- GENERATED OFFLINE: {POLICY_VERSION}\n"):
        blockers.append("probe_header_invalid")
        return _verdict(sql, blockers)
    lowered = sql.lower()
    required = (
        "begin;", "set local statement_timeout", "membership_probe_executor_invalid",
        "membership_probe_staging_not_clean",
        f"create role {ROLE_OWNER} nologin noinherit nosuperuser",
        f"create role {ROLE_WRITER} nologin noinherit nosuperuser",
        f"revoke {ROLE_OWNER} from postgres;", f"revoke {ROLE_WRITER} from postgres;",
        "from pg_catalog.pg_auth_members", "join pg_catalog.pg_roles granted_role",
        "join pg_catalog.pg_roles member_role", "join pg_catalog.pg_roles grantor_role",
        "membership_probe_result:%", "rollback;",
    )
    if any(fragment not in lowered for fragment in required):
        blockers.append("required_probe_guard_missing")
    if lowered.count("begin;") != 1 or lowered.count("rollback;") != 1:
        blockers.append("transaction_boundary_invalid")
    if sql.rstrip().splitlines()[-1].strip().lower() != "rollback;":
        blockers.append("final_rollback_missing")
    forbidden = (
        "commit;", "create schema", "create table", "create function", "create extension",
        "alter publication", "insert into", "update ", "delete from", "truncate ",
        "append_decision_outcome", "investment_decision_shadow", "supabase_url",
        "service_role_key", "postgresql://", "https://", "telegram", "investment_advice",
    )
    if any(fragment in lowered for fragment in forbidden):
        blockers.append("external_formal_or_data_operation_forbidden")
    return _verdict(sql, blockers)


def _verdict(sql: Any, blockers: list[str]) -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "mode": "research_only",
            "valid": not blockers, "sqlHash": _hash(sql) if isinstance(sql, str) else None,
            "blockers": sorted(set(blockers))}
