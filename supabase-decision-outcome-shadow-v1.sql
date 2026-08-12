-- OFFLINE E1C-B1 CONTRACT ONLY. DO NOT AUTO-EXECUTE.
-- A later staging-only node must separately authorise, preflight and apply it.
-- semantic-contract-sha256: e4c9e68f5bc4396c697f4432d958dca93485eeecaf7b88c1273fe75707b1cf0c

do $preflight$
declare
  checked_role record;
  role_name text;
begin
  if current_user <> 'postgres' then
    raise exception 'decision_outcome_requires_postgres_migration_role';
  end if;
  if not exists (
    select 1 from pg_catalog.pg_extension e
    join pg_catalog.pg_namespace n on n.oid = e.extnamespace
    where e.extname = 'pgcrypto' and n.nspname = 'extensions'
  ) or pg_catalog.to_regprocedure('extensions.digest(bytea,text)') is null then
    raise exception 'decision_outcome_pgcrypto_precondition_failed';
  end if;
  foreach role_name in array array['decision_outcome_owner','decision_outcome_writer'] loop
    select * into checked_role from pg_catalog.pg_roles where rolname = role_name;
    if not found then raise exception 'decision_outcome_required_role_missing'; end if;
    if checked_role.rolcanlogin or checked_role.rolinherit or checked_role.rolsuper
       or checked_role.rolbypassrls or checked_role.rolcreatedb
       or checked_role.rolcreaterole or checked_role.rolreplication then
      raise exception 'decision_outcome_role_attributes_unsafe';
    end if;
    if pg_catalog.has_schema_privilege(role_name, 'public', 'CREATE') then
      raise exception 'decision_outcome_public_schema_create_unsafe';
    end if;
  end loop;
  if exists (
    select 1 from pg_catalog.pg_auth_members m
    join pg_catalog.pg_roles granted_role on granted_role.oid = m.roleid
    join pg_catalog.pg_roles member_role on member_role.oid = m.member
    where granted_role.rolname in ('decision_outcome_owner','decision_outcome_writer')
       or member_role.rolname in ('decision_outcome_owner','decision_outcome_writer')
  ) then raise exception 'decision_outcome_role_membership_unsafe'; end if;
  if pg_catalog.to_regnamespace('investment_decision_shadow_v1') is not null
     or pg_catalog.to_regprocedure('public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)') is not null
     or pg_catalog.to_regprocedure('public.reject_decision_outcome_mutation_v1()') is not null then
    raise exception 'decision_outcome_migration_target_exists';
  end if;
end;
$preflight$;

create schema investment_decision_shadow_v1 authorization decision_outcome_owner;
revoke all on schema investment_decision_shadow_v1
  from public, anon, authenticated, service_role, decision_outcome_writer;

create table investment_decision_shadow_v1.event_blob_v1 (
  event_hash text primary key check (event_hash ~ '^[0-9a-f]{64}$'),
  event_type text not null check (event_type in ('decision_candidate','outcome_candidate','legacy_candidate')),
  logical_key_hash text not null check (logical_key_hash ~ '^[0-9a-f]{64}$'),
  canonical_text text not null,
  transport_blob_hash text not null unique check (transport_blob_hash ~ '^[0-9a-f]{64}$'),
  schema_version integer not null check (schema_version = 1),
  policy_version text not null check (policy_version = 'decision-outcome-event-candidate-v1'),
  diagnostic_only boolean not null check (diagnostic_only),
  promotion_eligible boolean not null check (not promotion_eligible),
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);

create table investment_decision_shadow_v1.manifest_blob_v1 (
  manifest_digest text primary key check (manifest_digest ~ '^[0-9a-f]{64}$'),
  scope_id text not null check (scope_id ~ '^[0-9a-f]{64}$'),
  event_set_digest text not null check (event_set_digest ~ '^[0-9a-f]{64}$'),
  canonical_text text not null,
  transport_blob_hash text not null unique check (transport_blob_hash ~ '^[0-9a-f]{64}$'),
  expected_event_count integer not null check (expected_event_count >= 0),
  expected_decision_count integer not null check (expected_decision_count >= 0),
  expected_outcome_count integer not null check (expected_outcome_count >= 0),
  expected_legacy_count integer not null check (expected_legacy_count between 0 and 1),
  decision_date_count integer not null check (decision_date_count >= 0),
  schema_version integer not null check (schema_version = 1),
  policy_version text not null check (policy_version = 'decision-outcome-frozen-manifest-v1'),
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);

create table investment_decision_shadow_v1.anchor_v1 (
  scope_id text not null check (scope_id ~ '^[0-9a-f]{64}$'),
  sequence bigint not null check (sequence >= 1),
  anchor_hash text not null unique check (anchor_hash ~ '^[0-9a-f]{64}$'),
  previous_anchor_hash text not null check (previous_anchor_hash ~ '^[0-9a-f]{64}$'),
  manifest_digest text not null references investment_decision_shadow_v1.manifest_blob_v1(manifest_digest),
  canonical_text text not null,
  transport_blob_hash text not null unique check (transport_blob_hash ~ '^[0-9a-f]{64}$'),
  expected_event_count integer not null check (expected_event_count >= 0),
  expected_decision_count integer not null check (expected_decision_count >= 0),
  expected_outcome_count integer not null check (expected_outcome_count >= 0),
  expected_legacy_count integer not null check (expected_legacy_count between 0 and 1),
  decision_date_count integer not null check (decision_date_count >= 0),
  schema_version integer not null check (schema_version = 1),
  policy_version text not null check (policy_version = 'decision-outcome-sandbox-ledger-v1'),
  diagnostic_only boolean not null check (diagnostic_only),
  promotion_eligible boolean not null check (not promotion_eligible),
  completeness_externally_anchored boolean not null check (not completeness_externally_anchored),
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  primary key (scope_id, sequence)
);

create table investment_decision_shadow_v1.audit_v1 (
  audit_id bigint generated always as identity primary key,
  scope_id text not null check (scope_id ~ '^[0-9a-f]{64}$'),
  sequence bigint not null check (sequence >= 1),
  anchor_hash text not null check (anchor_hash ~ '^[0-9a-f]{64}$'),
  manifest_digest text not null check (manifest_digest ~ '^[0-9a-f]{64}$'),
  receipt_hash text not null check (receipt_hash ~ '^[0-9a-f]{64}$'),
  result text not null check (result in ('inserted','duplicate')),
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);

alter table investment_decision_shadow_v1.event_blob_v1 enable row level security;
alter table investment_decision_shadow_v1.event_blob_v1 force row level security;
alter table investment_decision_shadow_v1.manifest_blob_v1 enable row level security;
alter table investment_decision_shadow_v1.manifest_blob_v1 force row level security;
alter table investment_decision_shadow_v1.anchor_v1 enable row level security;
alter table investment_decision_shadow_v1.anchor_v1 force row level security;
alter table investment_decision_shadow_v1.audit_v1 enable row level security;
alter table investment_decision_shadow_v1.audit_v1 force row level security;

revoke all on all tables in schema investment_decision_shadow_v1
  from public, anon, authenticated, service_role, decision_outcome_owner, decision_outcome_writer;
revoke all on all sequences in schema investment_decision_shadow_v1
  from public, anon, authenticated, service_role, decision_outcome_owner, decision_outcome_writer;
grant select, insert on investment_decision_shadow_v1.event_blob_v1,
  investment_decision_shadow_v1.manifest_blob_v1,
  investment_decision_shadow_v1.anchor_v1 to decision_outcome_owner;
grant insert on investment_decision_shadow_v1.audit_v1 to decision_outcome_owner;
grant usage on sequence investment_decision_shadow_v1.audit_v1_audit_id_seq to decision_outcome_owner;

create policy decision_outcome_event_owner_v1 on investment_decision_shadow_v1.event_blob_v1
  for all to decision_outcome_owner using (true) with check (diagnostic_only and not promotion_eligible);
create policy decision_outcome_manifest_owner_v1 on investment_decision_shadow_v1.manifest_blob_v1
  for all to decision_outcome_owner using (true) with check (expected_legacy_count between 0 and 1);
create policy decision_outcome_anchor_owner_v1 on investment_decision_shadow_v1.anchor_v1
  for all to decision_outcome_owner using (true)
  with check (diagnostic_only and not promotion_eligible and not completeness_externally_anchored);
create policy decision_outcome_audit_owner_v1 on investment_decision_shadow_v1.audit_v1
  for insert to decision_outcome_owner with check (result in ('inserted','duplicate'));

create function public.reject_decision_outcome_mutation_v1()
returns trigger language plpgsql security invoker set search_path = pg_catalog as $$
begin raise exception 'decision_outcome_append_only_mutation_forbidden'; end;
$$;
revoke all on function public.reject_decision_outcome_mutation_v1()
  from public, anon, authenticated, service_role, decision_outcome_owner, decision_outcome_writer;

create trigger reject_event_blob_mutation_v1 before update or delete or truncate
  on investment_decision_shadow_v1.event_blob_v1
  for each statement execute function public.reject_decision_outcome_mutation_v1();
create trigger reject_manifest_blob_mutation_v1 before update or delete or truncate
  on investment_decision_shadow_v1.manifest_blob_v1
  for each statement execute function public.reject_decision_outcome_mutation_v1();
create trigger reject_anchor_mutation_v1 before update or delete or truncate
  on investment_decision_shadow_v1.anchor_v1
  for each statement execute function public.reject_decision_outcome_mutation_v1();
create trigger reject_audit_mutation_v1 before update or delete or truncate
  on investment_decision_shadow_v1.audit_v1
  for each statement execute function public.reject_decision_outcome_mutation_v1();

grant usage on schema extensions to decision_outcome_owner;
grant execute on function extensions.digest(bytea,text) to decision_outcome_owner;

create function public.append_decision_outcome_snapshot_v1(
  p_events jsonb, p_manifest_text text, p_manifest_transport_hash text,
  p_anchor_text text, p_anchor_transport_hash text
) returns jsonb
language plpgsql security definer set search_path = pg_catalog as $$
declare
  manifest jsonb;
  anchor jsonb;
  item jsonb;
  existing_event investment_decision_shadow_v1.event_blob_v1%rowtype;
  existing_manifest investment_decision_shadow_v1.manifest_blob_v1%rowtype;
  existing_anchor investment_decision_shadow_v1.anchor_v1%rowtype;
  prior_anchor investment_decision_shadow_v1.anchor_v1%rowtype;
  prior_manifest jsonb;
  event_hashes jsonb;
  computed text;
  receipt_hash text;
  item_count bigint;
begin
  if pg_catalog.jsonb_typeof(p_events) <> 'array' then
    raise exception 'decision_outcome_events_contract_invalid';
  end if;
  computed := pg_catalog.encode(extensions.digest(pg_catalog.convert_to(p_manifest_text,'UTF8'),'sha256'),'hex');
  if computed <> p_manifest_transport_hash then raise exception 'decision_outcome_manifest_transport_invalid'; end if;
  computed := pg_catalog.encode(extensions.digest(pg_catalog.convert_to(p_anchor_text,'UTF8'),'sha256'),'hex');
  if computed <> p_anchor_transport_hash then raise exception 'decision_outcome_anchor_transport_invalid'; end if;
  manifest := p_manifest_text::jsonb;
  anchor := p_anchor_text::jsonb;
  if manifest->>'policyVersion' <> 'decision-outcome-frozen-manifest-v1'
     or anchor->>'policyVersion' <> 'decision-outcome-sandbox-ledger-v1'
     or anchor->>'manifestDigest' <> manifest->>'manifestDigest'
     or anchor->>'scopeId' <> manifest->>'scopeId'
     or (anchor->>'promotionEligible')::boolean
     or (anchor->>'completenessExternallyAnchored')::boolean
     or not (anchor->>'diagnosticOnly')::boolean then
    raise exception 'decision_outcome_snapshot_contract_invalid';
  end if;
  if (manifest->>'expectedLegacyCount')::integer not between 0 and 1
     or (manifest->>'expectedEventCount')::integer < 0
     or pg_catalog.jsonb_typeof(manifest->'eventHashes') <> 'array' then
    raise exception 'decision_outcome_manifest_contract_invalid';
  end if;
  select pg_catalog.count(*), pg_catalog.jsonb_agg(value order by value)
    into item_count, event_hashes from pg_catalog.jsonb_array_elements_text(manifest->'eventHashes');
  if item_count <> (manifest->>'expectedEventCount')::bigint
     or item_count <> (select pg_catalog.count(distinct value) from pg_catalog.jsonb_array_elements_text(manifest->'eventHashes'))
     or event_hashes <> (
       select pg_catalog.jsonb_agg(extracted.event_hash order by extracted.event_hash)
       from (
         select item->>'eventHash' as event_hash
         from pg_catalog.jsonb_array_elements(p_events) as source(item)
       ) as extracted
     ) then
    raise exception 'decision_outcome_event_set_invalid';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(anchor->>'scopeId', 0));
  select * into existing_anchor from investment_decision_shadow_v1.anchor_v1
   where scope_id = anchor->>'scopeId' and sequence = (anchor->>'sequence')::bigint;
  if found then
    if existing_anchor.anchor_hash = anchor->>'anchorHash'
       and existing_anchor.canonical_text = p_anchor_text
       and existing_anchor.transport_blob_hash = p_anchor_transport_hash then
      receipt_hash := pg_catalog.encode(extensions.digest(pg_catalog.convert_to(pg_catalog.concat_ws(pg_catalog.chr(31),
        anchor->>'scopeId', anchor->>'sequence', anchor->>'anchorHash', anchor->>'previousAnchorHash',
        manifest->>'manifestDigest', manifest->>'expectedEventCount', manifest->>'decisionDateCount',
        'decision-outcome-db-receipt-v1'), 'UTF8'),'sha256'),'hex');
      insert into investment_decision_shadow_v1.audit_v1
        (scope_id,sequence,anchor_hash,manifest_digest,receipt_hash,result)
      values (anchor->>'scopeId',(anchor->>'sequence')::bigint,anchor->>'anchorHash',
              manifest->>'manifestDigest',receipt_hash,'duplicate');
      return pg_catalog.jsonb_build_object('schemaVersion',1,'policyVersion','decision-outcome-db-receipt-v1',
        'scopeId',anchor->>'scopeId','sequence',(anchor->>'sequence')::bigint,
        'anchorHash',anchor->>'anchorHash','previousAnchorHash',anchor->>'previousAnchorHash',
        'manifestDigest',manifest->>'manifestDigest','eventCount',(manifest->>'expectedEventCount')::integer,
        'decisionDateCount',(manifest->>'decisionDateCount')::integer,'receiptHash',receipt_hash,
        'status','duplicate','diagnosticOnly',true,'promotionEligible',false);
    end if;
    raise exception 'decision_outcome_sequence_conflict';
  end if;

  select * into prior_anchor from investment_decision_shadow_v1.anchor_v1
   where scope_id = anchor->>'scopeId' order by sequence desc limit 1;
  if found then
    if (anchor->>'sequence')::bigint <> prior_anchor.sequence + 1
       or anchor->>'previousAnchorHash' <> prior_anchor.anchor_hash then
      raise exception 'decision_outcome_parent_or_sequence_conflict';
    end if;
    select canonical_text::jsonb into prior_manifest
      from investment_decision_shadow_v1.manifest_blob_v1
      where manifest_digest = prior_anchor.manifest_digest;
    if not (prior_manifest->'eventHashes' <@ manifest->'eventHashes')
       or (manifest->>'expectedEventCount')::integer < prior_anchor.expected_event_count
       or (manifest->>'expectedDecisionCount')::integer < prior_anchor.expected_decision_count
       or (manifest->>'expectedOutcomeCount')::integer < prior_anchor.expected_outcome_count
       or (manifest->>'expectedLegacyCount')::integer < prior_anchor.expected_legacy_count
       or (manifest->>'decisionDateCount')::integer < prior_anchor.decision_date_count then
      raise exception 'decision_outcome_snapshot_regression';
    end if;
  elsif (anchor->>'sequence')::bigint <> 1
        or anchor->>'previousAnchorHash' <> pg_catalog.repeat('0',64) then
    raise exception 'decision_outcome_genesis_invalid';
  end if;

  for item in select value from pg_catalog.jsonb_array_elements(p_events) loop
    if pg_catalog.jsonb_typeof(item) <> 'object'
       or item->>'eventHash' !~ '^[0-9a-f]{64}$'
       or item->>'transportBlobHash' !~ '^[0-9a-f]{64}$'
       or item->>'eventType' not in ('decision_candidate','outcome_candidate','legacy_candidate')
       or item->>'logicalKeyHash' !~ '^[0-9a-f]{64}$'
       or pg_catalog.encode(extensions.digest(pg_catalog.convert_to(item->>'canonicalText','UTF8'),'sha256'),'hex')
          <> item->>'transportBlobHash' then
      raise exception 'decision_outcome_event_transport_invalid';
    end if;
    select * into existing_event from investment_decision_shadow_v1.event_blob_v1
      where event_hash = item->>'eventHash';
    if found then
      if existing_event.canonical_text <> item->>'canonicalText'
         or existing_event.transport_blob_hash <> item->>'transportBlobHash' then
        raise exception 'decision_outcome_event_identity_conflict';
      end if;
    else
      insert into investment_decision_shadow_v1.event_blob_v1
        (event_hash,event_type,logical_key_hash,canonical_text,transport_blob_hash,
         schema_version,policy_version,diagnostic_only,promotion_eligible)
      values (item->>'eventHash',item->>'eventType',item->>'logicalKeyHash',
              item->>'canonicalText',item->>'transportBlobHash',1,
              'decision-outcome-event-candidate-v1',true,false);
    end if;
  end loop;

  select * into existing_manifest from investment_decision_shadow_v1.manifest_blob_v1
    where manifest_digest = manifest->>'manifestDigest';
  if found then
    if existing_manifest.canonical_text <> p_manifest_text
       or existing_manifest.transport_blob_hash <> p_manifest_transport_hash then
      raise exception 'decision_outcome_manifest_identity_conflict';
    end if;
  else
    insert into investment_decision_shadow_v1.manifest_blob_v1
      (manifest_digest,scope_id,event_set_digest,canonical_text,transport_blob_hash,
       expected_event_count,expected_decision_count,expected_outcome_count,
       expected_legacy_count,decision_date_count,schema_version,policy_version)
    values (manifest->>'manifestDigest',manifest->>'scopeId',anchor->>'eventSetDigest',
      p_manifest_text,p_manifest_transport_hash,(manifest->>'expectedEventCount')::integer,
      (manifest->>'expectedDecisionCount')::integer,(manifest->>'expectedOutcomeCount')::integer,
      (manifest->>'expectedLegacyCount')::integer,(manifest->>'decisionDateCount')::integer,
      1,'decision-outcome-frozen-manifest-v1');
  end if;

  insert into investment_decision_shadow_v1.anchor_v1
    (scope_id,sequence,anchor_hash,previous_anchor_hash,manifest_digest,canonical_text,
     transport_blob_hash,expected_event_count,expected_decision_count,
     expected_outcome_count,expected_legacy_count,decision_date_count,schema_version,
     policy_version,diagnostic_only,promotion_eligible,completeness_externally_anchored)
  values (anchor->>'scopeId',(anchor->>'sequence')::bigint,anchor->>'anchorHash',
    anchor->>'previousAnchorHash',manifest->>'manifestDigest',p_anchor_text,p_anchor_transport_hash,
    (manifest->>'expectedEventCount')::integer,(manifest->>'expectedDecisionCount')::integer,
    (manifest->>'expectedOutcomeCount')::integer,(manifest->>'expectedLegacyCount')::integer,
    (manifest->>'decisionDateCount')::integer,1,'decision-outcome-sandbox-ledger-v1',true,false,false);
  receipt_hash := pg_catalog.encode(extensions.digest(pg_catalog.convert_to(pg_catalog.concat_ws(pg_catalog.chr(31),
    anchor->>'scopeId',anchor->>'sequence',anchor->>'anchorHash',anchor->>'previousAnchorHash',
    manifest->>'manifestDigest',manifest->>'expectedEventCount',manifest->>'decisionDateCount',
    'decision-outcome-db-receipt-v1'), 'UTF8'),'sha256'),'hex');
  insert into investment_decision_shadow_v1.audit_v1
    (scope_id,sequence,anchor_hash,manifest_digest,receipt_hash,result)
  values (anchor->>'scopeId',(anchor->>'sequence')::bigint,anchor->>'anchorHash',
          manifest->>'manifestDigest',receipt_hash,'inserted');
  return pg_catalog.jsonb_build_object('schemaVersion',1,'policyVersion','decision-outcome-db-receipt-v1',
    'scopeId',anchor->>'scopeId','sequence',(anchor->>'sequence')::bigint,
    'anchorHash',anchor->>'anchorHash','previousAnchorHash',anchor->>'previousAnchorHash',
    'manifestDigest',manifest->>'manifestDigest','eventCount',(manifest->>'expectedEventCount')::integer,
    'decisionDateCount',(manifest->>'decisionDateCount')::integer,'receiptHash',receipt_hash,
    'status','inserted','diagnosticOnly',true,'promotionEligible',false);
end;
$$;

revoke all on function public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)
  from public, anon, authenticated, service_role, decision_outcome_owner, decision_outcome_writer;
alter function public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)
  owner to decision_outcome_owner;
grant usage on schema public to decision_outcome_writer;
grant execute on function public.append_decision_outcome_snapshot_v1(jsonb,text,text,text,text)
  to decision_outcome_writer;
