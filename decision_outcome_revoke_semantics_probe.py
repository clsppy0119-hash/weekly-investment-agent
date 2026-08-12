"""Build one bounded, rollback-only grantor-aware membership diagnostic.

The probe exists only to test PostgreSQL/Supabase role-membership semantics in
an already verified staging project.  It creates two fixed NOLOGIN roles,
requires the exact creator edges observed by the prior diagnostic, attempts an
exact ``GRANTED BY supabase_admin`` cleanup, proves the scoped catalog is empty,
then raises an intentional exception so every temporary change is rolled back.

It never runs a migration, creates application objects, reads application
rows, connects to a database, or changes advice/backtest/notification flows.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


SCHEMA_VERSION = 1
POLICY_VERSION = "decision-outcome-revoke-semantics-probe-v1"
ROLE_OWNER = "decision_outcome_owner"
ROLE_WRITER = "decision_outcome_writer"
EXPECTED_MEMBER = "postgres"
EXPECTED_GRANTOR = "supabase_admin"
CANONICAL_SQL_SHA256 = "7887f33d388f09f4f0f82466d73279dbdbaeaf8fa5700a648f013850ca01ec95"
CANONICAL_SQL_BYTES = 3387


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build(*, enabled: bool = False) -> dict[str, Any]:
    """Return deterministic SQL; disabled mode emits no executable content."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mode": "disabled",
            "ready": False,
            "sql": None,
            "sqlHash": None,
            "blockers": ["feature_disabled"],
        }

    sql = f"""-- GENERATED OFFLINE: {POLICY_VERSION}
-- Run once only in the separately verified staging project as postgres.
begin;
set local statement_timeout = '15s';
set local lock_timeout = '5s';
set local idle_in_transaction_session_timeout = '15s';

do $revoke_semantics_probe$
declare
  observed_summary jsonb;
  scoped_count integer;
  exact_count integer;
  remaining_count integer;
begin
  if current_user <> '{EXPECTED_MEMBER}' then
    raise exception 'revoke_semantics_probe_executor_invalid';
  end if;
  if pg_catalog.to_regrole('{ROLE_OWNER}') is not null
     or pg_catalog.to_regrole('{ROLE_WRITER}') is not null then
    raise exception 'revoke_semantics_probe_staging_not_clean';
  end if;

  create role {ROLE_OWNER} nologin noinherit nosuperuser nocreatedb
    nocreaterole noreplication nobypassrls;
  create role {ROLE_WRITER} nologin noinherit nosuperuser nocreatedb
    nocreaterole noreplication nobypassrls;

  select
    pg_catalog.count(*),
    pg_catalog.count(*) filter (
      where member_role.rolname = '{EXPECTED_MEMBER}'
        and grantor_role.rolname = '{EXPECTED_GRANTOR}'
        and m.admin_option
        and not m.inherit_option
        and not m.set_option
    ),
    coalesce(
      pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'grantedRole', granted_role.rolname,
          'memberRole', member_role.rolname,
          'grantorRole', grantor_role.rolname,
          'options', pg_catalog.jsonb_build_object(
            'adminOption', m.admin_option,
            'inheritOption', m.inherit_option,
            'setOption', m.set_option
          )
        ) order by granted_role.rolname
      ), '[]'::jsonb
    )
  into scoped_count, exact_count, observed_summary
  from pg_catalog.pg_auth_members m
  join pg_catalog.pg_roles granted_role on granted_role.oid = m.roleid
  join pg_catalog.pg_roles member_role on member_role.oid = m.member
  join pg_catalog.pg_roles grantor_role on grantor_role.oid = m.grantor
  where granted_role.rolname in ('{ROLE_OWNER}','{ROLE_WRITER}')
     or member_role.rolname in ('{ROLE_OWNER}','{ROLE_WRITER}');

  if scoped_count <> 2 or exact_count <> 2
     or observed_summary->0->>'grantedRole' <> '{ROLE_OWNER}'
     or observed_summary->1->>'grantedRole' <> '{ROLE_WRITER}' then
    raise exception 'revoke_semantics_probe_creator_edges_unexpected';
  end if;

  revoke {ROLE_OWNER} from {EXPECTED_MEMBER}
    granted by {EXPECTED_GRANTOR} restrict;
  revoke {ROLE_WRITER} from {EXPECTED_MEMBER}
    granted by {EXPECTED_GRANTOR} restrict;

  select pg_catalog.count(*) into remaining_count
  from pg_catalog.pg_auth_members m
  join pg_catalog.pg_roles granted_role on granted_role.oid = m.roleid
  join pg_catalog.pg_roles member_role on member_role.oid = m.member
  where granted_role.rolname in ('{ROLE_OWNER}','{ROLE_WRITER}')
     or member_role.rolname in ('{ROLE_OWNER}','{ROLE_WRITER}');

  if remaining_count <> 0 then
    raise exception 'revoke_semantics_probe_cleanup_incomplete';
  end if;

  raise exception 'revoke_semantics_probe_result:%',
    pg_catalog.jsonb_build_object(
      'observed', observed_summary,
      'remainingCount', remaining_count,
      'cleanupVerified', true
    );
end;
$revoke_semantics_probe$;

rollback;
"""
    checked = validate(sql, enabled=True)
    if not checked["valid"]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mode": "research_only",
            "ready": False,
            "sql": None,
            "sqlHash": None,
            "blockers": checked["blockers"],
        }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "research_only",
        "ready": True,
        "sql": sql,
        "sqlHash": _hash(sql),
        "blockers": [],
        "expectedResult": "intentional_exception_then_zero_residue_check",
        "diagnosticOnly": True,
        "researchOnly": True,
        "promotionEligible": False,
        "pitCertified": False,
        "productionApproved": False,
        "strategyValidated": False,
    }


def validate(sql: Any, *, enabled: bool = False) -> dict[str, Any]:
    """Fail closed unless the SQL is the exact bounded diagnostic shape."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mode": "disabled",
            "valid": False,
            "sqlHash": None,
            "blockers": ["feature_disabled"],
        }
    blockers: list[str] = []
    if not isinstance(sql, str) or not sql.startswith(
        f"-- GENERATED OFFLINE: {POLICY_VERSION}\n"
    ):
        blockers.append("probe_header_invalid")
        return _verdict(sql, blockers)

    lowered = sql.lower()
    if _hash(sql) != CANONICAL_SQL_SHA256 or len(sql.encode("utf-8")) != CANONICAL_SQL_BYTES:
        blockers.append("canonical_sql_mismatch")
    required = (
        "begin;",
        "set local statement_timeout = '15s'",
        "set local lock_timeout = '5s'",
        "set local idle_in_transaction_session_timeout = '15s'",
        "revoke_semantics_probe_executor_invalid",
        "revoke_semantics_probe_staging_not_clean",
        f"if current_user <> '{EXPECTED_MEMBER}' then",
        f"if pg_catalog.to_regrole('{ROLE_OWNER}') is not null",
        f"or pg_catalog.to_regrole('{ROLE_WRITER}') is not null then",
        f"create role {ROLE_OWNER} nologin noinherit nosuperuser",
        f"create role {ROLE_WRITER} nologin noinherit nosuperuser",
        "from pg_catalog.pg_auth_members",
        "join pg_catalog.pg_roles granted_role",
        "join pg_catalog.pg_roles member_role",
        "join pg_catalog.pg_roles grantor_role",
        f"member_role.rolname = '{EXPECTED_MEMBER}'",
        f"grantor_role.rolname = '{EXPECTED_GRANTOR}'",
        "and m.admin_option",
        "and not m.inherit_option",
        "and not m.set_option",
        "if scoped_count <> 2 or exact_count <> 2",
        f"observed_summary->0->>'grantedrole' <> '{ROLE_OWNER}'",
        f"observed_summary->1->>'grantedrole' <> '{ROLE_WRITER}'",
        "revoke_semantics_probe_creator_edges_unexpected",
        "if remaining_count <> 0",
        "revoke_semantics_probe_cleanup_incomplete",
        "revoke_semantics_probe_result:%",
        "'remainingcount', remaining_count",
        "'cleanupverified', true",
        "rollback;",
    )
    if any(fragment not in lowered for fragment in required):
        blockers.append("required_probe_guard_missing")

    expected_revokes = [
        (ROLE_OWNER, EXPECTED_MEMBER, EXPECTED_GRANTOR),
        (ROLE_WRITER, EXPECTED_MEMBER, EXPECTED_GRANTOR),
    ]
    revokes = re.findall(
        r"revoke\s+([a-z_][a-z0-9_]*)\s+from\s+([a-z_][a-z0-9_]*)\s+"
        r"granted\s+by\s+([a-z_][a-z0-9_]*)\s+restrict\s*;",
        lowered,
    )
    if revokes != expected_revokes:
        blockers.append("grantor_aware_revoke_invalid")
    if re.search(
        rf"revoke\s+(?:{ROLE_OWNER}|{ROLE_WRITER})\s+from\s+{EXPECTED_MEMBER}\s*;",
        lowered,
    ):
        blockers.append("unscoped_revoke_forbidden")

    normalized = re.sub(r"\s+", " ", lowered)
    expected_create_roles = (
        f"create role {ROLE_OWNER} nologin noinherit nosuperuser nocreatedb "
        "nocreaterole noreplication nobypassrls;",
        f"create role {ROLE_WRITER} nologin noinherit nosuperuser nocreatedb "
        "nocreaterole noreplication nobypassrls;",
    )
    if any(normalized.count(statement) != 1 for statement in expected_create_roles):
        blockers.append("temporary_role_attributes_invalid")

    relation_text = lowered
    for role in (ROLE_OWNER, ROLE_WRITER):
        relation_text = re.sub(
            rf"(?m)^\s*revoke\s+{role}\s+from\s+{EXPECTED_MEMBER}\s*\n"
            rf"\s*granted\s+by\s+{EXPECTED_GRANTOR}\s+restrict\s*;\s*$",
            "",
            relation_text,
        )
    relation_refs = re.findall(
        r"\b(from|join)\s+([a-z_][a-z0-9_.]*)",
        relation_text,
    )
    if relation_refs != [
        ("from", "pg_catalog.pg_auth_members"),
        ("join", "pg_catalog.pg_roles"),
        ("join", "pg_catalog.pg_roles"),
        ("join", "pg_catalog.pg_roles"),
        ("from", "pg_catalog.pg_auth_members"),
        ("join", "pg_catalog.pg_roles"),
        ("join", "pg_catalog.pg_roles"),
    ]:
        blockers.append("catalog_relation_allowlist_invalid")
    if lowered.count("create role ") != 2:
        blockers.append("temporary_role_scope_invalid")
    if len(re.findall(r"(?m)^\s*revoke\s+", lowered)) != 2:
        blockers.append("revoke_statement_inventory_invalid")

    scope = (
        f"where granted_role.rolname in ('{ROLE_OWNER}','{ROLE_WRITER}')\n"
        f"     or member_role.rolname in ('{ROLE_OWNER}','{ROLE_WRITER}')"
    )
    if lowered.count(scope) != 2:
        blockers.append("membership_scope_guard_invalid")
    catalog_positions = [
        match.start()
        for match in re.finditer("from pg_catalog.pg_auth_members", lowered)
    ]
    revoke_positions = [
        lowered.find(f"revoke {role} from {EXPECTED_MEMBER}")
        for role in (ROLE_OWNER, ROLE_WRITER)
    ]
    if (
        len(catalog_positions) != 2
        or any(position < 0 for position in revoke_positions)
        or not (
            catalog_positions[0]
            < revoke_positions[0]
            < revoke_positions[1]
            < catalog_positions[1]
        )
    ):
        blockers.append("observe_revoke_verify_order_invalid")

    if lowered.count("begin;") != 1 or lowered.count("rollback;") != 1:
        blockers.append("transaction_boundary_invalid")
    if sql.rstrip().splitlines()[-1].strip().lower() != "rollback;":
        blockers.append("final_rollback_missing")

    forbidden = (
        "commit;",
        " cascade",
        "set role",
        "set session authorization",
        "create schema",
        "create table",
        "create function",
        "create extension",
        "create view",
        "create materialized view",
        "grant ",
        "alter role",
        "drop role",
        "alter schema",
        "drop schema",
        "alter table",
        "drop table",
        "alter function",
        "drop function",
        "alter extension",
        "drop extension",
        "alter publication",
        "insert into",
        "update ",
        "delete from",
        "truncate ",
        "append_decision_outcome",
        "investment_decision_shadow",
        "supabase_url",
        "service_role_key",
        "postgresql://",
        "https://",
        "telegram",
        "investment_advice",
        "candidate_manifest",
        "strategy_tracker",
        "promotion_status",
        "backtest",
        "availableat",
        "pitcertified",
        "public.",
        "to_jsonb(m)",
        "'oid'",
        "'roleid'",
        "'member'",
        "'grantor'",
    )
    if any(fragment in lowered for fragment in forbidden):
        blockers.append("fallback_formal_or_data_operation_forbidden")
    if re.search(r"\b(?:[a-z_][a-z0-9_]*\.)+coalesce\s*\(", lowered):
        blockers.append("schema_qualified_coalesce_forbidden")

    option_keys = re.findall(
        r"'([a-zA-Z][a-zA-Z0-9]*)'\s*,\s*m\.(admin_option|inherit_option|set_option)",
        sql,
    )
    if option_keys != [
        ("adminOption", "admin_option"),
        ("inheritOption", "inherit_option"),
        ("setOption", "set_option"),
    ]:
        blockers.append("membership_option_allowlist_invalid")
    return _verdict(sql, blockers)


def _verdict(sql: Any, blockers: list[str]) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "research_only",
        "valid": not blockers,
        "sqlHash": _hash(sql) if isinstance(sql, str) else None,
        "blockers": sorted(set(blockers)),
        "productionApproved": False,
        "strategyValidated": False,
        "diagnosticOnly": True,
        "researchOnly": True,
        "promotionEligible": False,
        "pitCertified": False,
    }
