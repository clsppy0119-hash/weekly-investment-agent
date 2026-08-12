-- OFFLINE CONTRACT ONLY. DO NOT AUTO-EXECUTE.
-- Preconditions for a later manual node: pgcrypto, non-login owner role
-- lineage_observer_owner, and scoped caller role lineage_observer_writer.
-- A Supabase service-role token is explicitly NOT an acceptable routine caller.

create table if not exists public.investment_announcement_first_seen_shadow_v1 (
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
  metadata jsonb not null check (metadata ? 'limitations' and metadata ? 'classification'),
  created_at timestamptz not null default now(),
  unique (provider, official_document_id, entity_id, source_revision),
  unique (record_hash)
);

create table if not exists public.investment_announcement_first_seen_audit_v1 (
  audit_id bigint generated always as identity primary key,
  composite_key text not null,
  record_hash text not null,
  result text not null check (result in ('inserted', 'duplicate')),
  created_at timestamptz not null default now()
);

alter table public.investment_announcement_first_seen_shadow_v1 enable row level security;
alter table public.investment_announcement_first_seen_shadow_v1 force row level security;
alter table public.investment_announcement_first_seen_audit_v1 enable row level security;
alter table public.investment_announcement_first_seen_audit_v1 force row level security;

revoke all on public.investment_announcement_first_seen_shadow_v1 from public, anon, authenticated, service_role, lineage_observer_writer;
revoke all on public.investment_announcement_first_seen_audit_v1 from public, anon, authenticated, service_role, lineage_observer_writer;

create or replace function public.reject_announcement_first_seen_mutation_v1()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
begin
  raise exception 'append_only_mutation_forbidden';
end;
$$;

create trigger reject_announcement_first_seen_update_delete_v1
before update or delete on public.investment_announcement_first_seen_shadow_v1
for each row execute function public.reject_announcement_first_seen_mutation_v1();

create trigger reject_announcement_first_seen_audit_update_delete_v1
before update or delete on public.investment_announcement_first_seen_audit_v1
for each row execute function public.reject_announcement_first_seen_mutation_v1();

create or replace function public.append_announcement_first_seen_shadow_v1(
  p_provider text, p_market text, p_source_contract_id text,
  p_official_document_id text, p_official_letter_no text, p_entity_id text,
  p_event_type text, p_effective_date date, p_source_revision text,
  p_content_hash text, p_first_seen_at timestamptz, p_supersedes_content_hash text,
  p_composite_key text, p_record_hash text, p_schema_version integer,
  p_policy_version text, p_evidence_mode text, p_visibility text, p_metadata jsonb
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
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
     or p_metadata <> jsonb_build_object(
       'classification', 'FORWARD_OBSERVED_ONLY',
       'limitations', jsonb_build_array('forward_only','no_historical_backfill','not_formal_advice_evidence')
     ) then
    raise exception 'announcement_first_seen_contract_invalid';
  end if;

  expected_composite_key := encode(digest(convert_to(concat_ws(chr(31),
    p_provider, p_source_contract_id, p_official_document_id, p_entity_id,
    p_event_type, p_effective_date::text, p_source_revision, p_schema_version::text
  ), 'UTF8'), 'sha256'), 'hex');
  if p_composite_key <> expected_composite_key then
    raise exception 'announcement_first_seen_composite_invalid';
  end if;
  expected_record_hash := encode(digest(convert_to(concat_ws(chr(31),
    p_provider, p_market, p_source_contract_id, p_official_document_id,
    p_official_letter_no, p_entity_id, p_event_type, p_effective_date::text,
    p_source_revision, p_content_hash,
    to_char(p_first_seen_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    coalesce(p_supersedes_content_hash, ''), p_composite_key, p_schema_version::text,
    p_policy_version, p_evidence_mode, p_visibility
  ), 'UTF8'), 'sha256'), 'hex');
  if p_record_hash <> expected_record_hash then
    raise exception 'announcement_first_seen_record_hash_invalid';
  end if;

  -- Serialize all revisions for the same official document/entity so two
  -- concurrent corrections cannot both supersede the same head.
  perform pg_advisory_xact_lock(hashtextextended(concat_ws(chr(31),
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
      return jsonb_build_object('status', 'duplicate', 'record_hash', p_record_hash);
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
  return jsonb_build_object('status', 'inserted', 'record_hash', p_record_hash);
end;
$$;

alter function public.append_announcement_first_seen_shadow_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) owner to lineage_observer_owner;

revoke all on function public.append_announcement_first_seen_shadow_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.append_announcement_first_seen_shadow_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) to lineage_observer_writer;
