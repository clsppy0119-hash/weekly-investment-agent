-- OFFLINE CONTRACT ONLY. DO NOT AUTO-EXECUTE.
-- B2A2 is a strict, single-use Supabase migration contract. A later manual
-- node must wrap it in one transaction after verifying this file's pinned hash.
-- It deliberately creates no login, password, API key, extension, or network path.

do $b2a2_preflight$
declare
  checked_role record;
  role_name text;
begin
  if current_user <> 'postgres' then
    raise exception 'b2a2_requires_postgres_migration_role';
  end if;
  if not exists (
    select 1 from pg_catalog.pg_extension e
    join pg_catalog.pg_namespace n on n.oid = e.extnamespace
    where e.extname = 'pgcrypto' and n.nspname = 'extensions'
  ) or pg_catalog.to_regprocedure('extensions.digest(bytea,text)') is null then
    raise exception 'b2a2_pgcrypto_extensions_precondition_failed';
  end if;

  foreach role_name in array array['lineage_observer_owner','lineage_observer_writer'] loop
    select * into checked_role from pg_catalog.pg_roles where rolname = role_name;
    if not found then
      raise exception 'b2a2_required_role_missing:%', role_name;
    end if;
    if checked_role.rolcanlogin or checked_role.rolinherit or checked_role.rolsuper
       or checked_role.rolbypassrls or checked_role.rolcreatedb
       or checked_role.rolcreaterole or checked_role.rolreplication then
      raise exception 'b2a2_role_attributes_unsafe:%', role_name;
    end if;
    if pg_catalog.has_schema_privilege(role_name, 'public', 'CREATE') then
      raise exception 'b2a2_public_schema_create_unsafe:%', role_name;
    end if;
  end loop;

  if exists (
    select 1 from pg_catalog.pg_auth_members m
    join pg_catalog.pg_roles granted_role on granted_role.oid = m.roleid
    join pg_catalog.pg_roles member_role on member_role.oid = m.member
    where granted_role.rolname in ('lineage_observer_owner','lineage_observer_writer')
       or member_role.rolname in ('lineage_observer_owner','lineage_observer_writer')
  ) then
    raise exception 'b2a2_role_membership_unsafe';
  end if;

  if pg_catalog.to_regnamespace('investment_lineage_admin_v1') is not null
     or pg_catalog.to_regclass('public.investment_announcement_first_seen_shadow_v1') is not null
     or pg_catalog.to_regclass('public.investment_announcement_first_seen_audit_v1') is not null
     or pg_catalog.to_regprocedure('public.reject_announcement_first_seen_mutation_v1()') is not null
     or pg_catalog.to_regprocedure('public.append_announcement_first_seen_shadow_v1(text,text,text,text,text,text,text,date,text,text,timestamp with time zone,text,text,text,integer,text,text,text,jsonb)') is not null then
    raise exception 'b2a2_migration_target_exists';
  end if;
end;
$b2a2_preflight$;

create schema investment_lineage_admin_v1 authorization postgres;
revoke all on schema investment_lineage_admin_v1 from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer;

create table investment_lineage_admin_v1.contract_migration_ledger_v1 (
  migration_id text primary key check (migration_id = 'announcement-first-seen-shadow-v1-b2a2'),
  migration_contract_version integer not null check (migration_contract_version = 2),
  schema_version integer not null check (schema_version = 1),
  semantic_contract_hash text not null check (semantic_contract_hash ~ '^[0-9a-f]{64}$'),
  applied_at timestamptz not null default pg_catalog.clock_timestamp()
);
revoke all on investment_lineage_admin_v1.contract_migration_ledger_v1 from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer;

create table public.investment_announcement_first_seen_shadow_v1 (
  provider text not null check (provider in ('TWSE', 'TPEX')),
  market text not null check (market in ('listed', 'otc', 'emerging')),
  source_contract_id text not null,
  official_document_id text not null,
  official_letter_no text not null,
  entity_id text not null,
  event_type text not null check (event_type in ('listing', 'delisting')),
  effective_date date not null,
  source_revision text not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  first_seen_at timestamptz not null,
  supersedes_content_hash text check (supersedes_content_hash is null or supersedes_content_hash ~ '^[0-9a-f]{64}$'),
  composite_key text primary key check (composite_key ~ '^[0-9a-f]{64}$'),
  record_hash text not null check (record_hash ~ '^[0-9a-f]{64}$'),
  schema_version integer not null check (schema_version = 1),
  policy_version text not null check (policy_version = 'official-announcement-first-seen-v1'),
  evidence_mode text not null check (evidence_mode = 'forward_observed_only'),
  visibility text not null check (visibility = 'private_lineage'),
  metadata jsonb not null check (metadata = pg_catalog.jsonb_build_object(
    'classification', 'FORWARD_OBSERVED_ONLY',
    'limitations', pg_catalog.jsonb_build_array('forward_only','no_historical_backfill','not_formal_advice_evidence')
  )),
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  unique (provider, official_document_id, entity_id, source_revision),
  unique (record_hash)
);

create table public.investment_announcement_first_seen_audit_v1 (
  audit_id bigint generated always as identity primary key,
  composite_key text not null check (composite_key ~ '^[0-9a-f]{64}$'),
  record_hash text not null check (record_hash ~ '^[0-9a-f]{64}$'),
  result text not null check (result in ('inserted', 'duplicate')),
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);

alter table public.investment_announcement_first_seen_shadow_v1 enable row level security;
alter table public.investment_announcement_first_seen_shadow_v1 force row level security;
alter table public.investment_announcement_first_seen_audit_v1 enable row level security;
alter table public.investment_announcement_first_seen_audit_v1 force row level security;

revoke all on public.investment_announcement_first_seen_shadow_v1 from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer;
revoke all on public.investment_announcement_first_seen_audit_v1 from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer;
revoke all on sequence public.investment_announcement_first_seen_audit_v1_audit_id_seq from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer;
grant select, insert on public.investment_announcement_first_seen_shadow_v1 to lineage_observer_owner;
grant insert on public.investment_announcement_first_seen_audit_v1 to lineage_observer_owner;
grant usage on sequence public.investment_announcement_first_seen_audit_v1_audit_id_seq to lineage_observer_owner;

create policy announcement_first_seen_owner_select_v1
on public.investment_announcement_first_seen_shadow_v1
for select to lineage_observer_owner using (true);

create policy announcement_first_seen_owner_insert_v1
on public.investment_announcement_first_seen_shadow_v1
for insert to lineage_observer_owner with check (
  provider in ('TWSE','TPEX') and market in ('listed','otc','emerging')
  and event_type in ('listing','delisting')
  and content_hash ~ '^[0-9a-f]{64}$' and composite_key ~ '^[0-9a-f]{64}$'
  and record_hash ~ '^[0-9a-f]{64}$'
  and (supersedes_content_hash is null or supersedes_content_hash ~ '^[0-9a-f]{64}$')
  and schema_version = 1 and policy_version = 'official-announcement-first-seen-v1'
  and evidence_mode = 'forward_observed_only' and visibility = 'private_lineage'
  and metadata = pg_catalog.jsonb_build_object(
    'classification', 'FORWARD_OBSERVED_ONLY',
    'limitations', pg_catalog.jsonb_build_array('forward_only','no_historical_backfill','not_formal_advice_evidence')
  )
);

create policy announcement_first_seen_audit_owner_insert_v1
on public.investment_announcement_first_seen_audit_v1
for insert to lineage_observer_owner with check (
  composite_key ~ '^[0-9a-f]{64}$' and record_hash ~ '^[0-9a-f]{64}$'
  and result in ('inserted','duplicate')
);

create function public.reject_announcement_first_seen_mutation_v1()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  raise exception 'append_only_mutation_forbidden';
end;
$$;
revoke all on function public.reject_announcement_first_seen_mutation_v1() from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer;

create trigger reject_announcement_first_seen_update_delete_v1
before update or delete on public.investment_announcement_first_seen_shadow_v1
for each row execute function public.reject_announcement_first_seen_mutation_v1();

create trigger reject_announcement_first_seen_audit_update_delete_v1
before update or delete on public.investment_announcement_first_seen_audit_v1
for each row execute function public.reject_announcement_first_seen_mutation_v1();

create trigger reject_announcement_first_seen_ledger_update_delete_v1
before update or delete on investment_lineage_admin_v1.contract_migration_ledger_v1
for each row execute function public.reject_announcement_first_seen_mutation_v1();

grant usage on schema extensions to lineage_observer_owner;
grant execute on function extensions.digest(bytea,text) to lineage_observer_owner;

create function public.append_announcement_first_seen_shadow_v1(
  p_provider text, p_market text, p_source_contract_id text,
  p_official_document_id text, p_official_letter_no text, p_entity_id text,
  p_event_type text, p_effective_date date, p_source_revision text,
  p_content_hash text, p_first_seen_at timestamptz, p_supersedes_content_hash text,
  p_composite_key text, p_record_hash text, p_schema_version integer,
  p_policy_version text, p_evidence_mode text, p_visibility text, p_metadata jsonb
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
  existing public.investment_announcement_first_seen_shadow_v1%rowtype;
  head_content_hash text;
  expected_composite_key text;
  expected_record_hash text;
begin
  if p_evidence_mode <> 'forward_observed_only' or p_visibility <> 'private_lineage'
     or p_schema_version <> 1 or p_policy_version <> 'official-announcement-first-seen-v1'
     or p_first_seen_at is null
     or p_metadata <> pg_catalog.jsonb_build_object(
       'classification', 'FORWARD_OBSERVED_ONLY',
       'limitations', pg_catalog.jsonb_build_array('forward_only','no_historical_backfill','not_formal_advice_evidence')
     ) then
    raise exception 'announcement_first_seen_contract_invalid';
  end if;

  expected_composite_key := pg_catalog.encode(extensions.digest(pg_catalog.convert_to(pg_catalog.concat_ws(pg_catalog.chr(31),
    p_provider, p_source_contract_id, p_official_document_id, p_entity_id,
    p_event_type, p_effective_date::text, p_source_revision, p_schema_version::text
  ), 'UTF8'), 'sha256'), 'hex');
  if p_composite_key <> expected_composite_key then
    raise exception 'announcement_first_seen_composite_invalid';
  end if;
  expected_record_hash := pg_catalog.encode(extensions.digest(pg_catalog.convert_to(pg_catalog.concat_ws(pg_catalog.chr(31),
    p_provider, p_market, p_source_contract_id, p_official_document_id,
    p_official_letter_no, p_entity_id, p_event_type, p_effective_date::text,
    p_source_revision, p_content_hash,
    pg_catalog.to_char(p_first_seen_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    pg_catalog.coalesce(p_supersedes_content_hash, ''), p_composite_key, p_schema_version::text,
    p_policy_version, p_evidence_mode, p_visibility
  ), 'UTF8'), 'sha256'), 'hex');
  if p_record_hash <> expected_record_hash then
    raise exception 'announcement_first_seen_record_hash_invalid';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(pg_catalog.concat_ws(pg_catalog.chr(31),
    p_provider, p_official_document_id, p_entity_id
  ), 0));
  select * into existing
  from public.investment_announcement_first_seen_shadow_v1
  where composite_key = p_composite_key;
  if found then
    if existing.content_hash = p_content_hash and existing.first_seen_at = p_first_seen_at
       and existing.record_hash = p_record_hash then
      insert into public.investment_announcement_first_seen_audit_v1
        (composite_key, record_hash, result) values (p_composite_key, p_record_hash, 'duplicate');
      return pg_catalog.jsonb_build_object('status', 'duplicate', 'record_hash', p_record_hash);
    end if;
    raise exception 'announcement_first_seen_identity_conflict';
  end if;

  select content_hash into head_content_hash
  from public.investment_announcement_first_seen_shadow_v1
  where provider = p_provider and official_document_id = p_official_document_id and entity_id = p_entity_id
  order by first_seen_at desc, created_at desc limit 1;
  if found and p_supersedes_content_hash is distinct from head_content_hash then
    raise exception 'announcement_first_seen_correction_conflict';
  elsif not found and p_supersedes_content_hash is not null then
    raise exception 'announcement_first_seen_unknown_supersedes';
  end if;

  insert into public.investment_announcement_first_seen_shadow_v1 (
    provider, market, source_contract_id, official_document_id, official_letter_no,
    entity_id, event_type, effective_date, source_revision, content_hash, first_seen_at,
    supersedes_content_hash, composite_key, record_hash, schema_version, policy_version,
    evidence_mode, visibility, metadata
  ) values (
    p_provider, p_market, p_source_contract_id, p_official_document_id, p_official_letter_no,
    p_entity_id, p_event_type, p_effective_date, p_source_revision, p_content_hash, p_first_seen_at,
    p_supersedes_content_hash, p_composite_key, p_record_hash, p_schema_version, p_policy_version,
    p_evidence_mode, p_visibility, p_metadata
  );
  insert into public.investment_announcement_first_seen_audit_v1
    (composite_key, record_hash, result) values (p_composite_key, p_record_hash, 'inserted');
  return pg_catalog.jsonb_build_object('status', 'inserted', 'record_hash', p_record_hash);
end;
$$;

revoke all on function public.append_announcement_first_seen_shadow_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer;
alter function public.append_announcement_first_seen_shadow_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) owner to lineage_observer_owner;
grant usage on schema public to lineage_observer_writer;
grant execute on function public.append_announcement_first_seen_shadow_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) to lineage_observer_writer;

insert into investment_lineage_admin_v1.contract_migration_ledger_v1 (
  migration_id, migration_contract_version, schema_version, semantic_contract_hash
) values (
  'announcement-first-seen-shadow-v1-b2a2', 2, 1,
  '0b52443aea14471851775b8da705cdb332319aa1daa18c2b6916209b5d79132c'
);
