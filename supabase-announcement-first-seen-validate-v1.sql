-- OFFLINE CONTRACT ONLY. DO NOT AUTO-EXECUTE.
-- B2B1 adds one pure payload validator. It performs no relation access,
-- mutation, clock/session lookup, network operation, or historical certification.

create function public.validate_announcement_first_seen_payload_v1(
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
  expected_composite_key text;
  expected_record_hash text;
begin
  if p_provider not in ('TWSE','TPEX')
     or p_market not in ('listed','otc','emerging')
     or p_event_type not in ('listing','delisting')
     or p_schema_version <> 1
     or p_policy_version <> 'official-announcement-first-seen-v1'
     or p_evidence_mode <> 'forward_observed_only'
     or p_visibility <> 'private_lineage'
     or p_first_seen_at is null
     or p_content_hash !~ '^[0-9a-f]{64}$'
     or p_composite_key !~ '^[0-9a-f]{64}$'
     or p_record_hash !~ '^[0-9a-f]{64}$'
     or (p_supersedes_content_hash is not null and p_supersedes_content_hash !~ '^[0-9a-f]{64}$')
     or p_metadata <> pg_catalog.jsonb_build_object(
       'classification', 'FORWARD_OBSERVED_ONLY',
       'limitations', pg_catalog.jsonb_build_array(
         'forward_only','no_historical_backfill','not_formal_advice_evidence'
       )
     ) then
    raise exception 'announcement_first_seen_validation_contract_invalid';
  end if;

  expected_composite_key := pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.concat_ws(pg_catalog.chr(31),
      p_provider, p_source_contract_id, p_official_document_id, p_entity_id,
      p_event_type, p_effective_date::text, p_source_revision, p_schema_version::text
    ), 'UTF8'), 'sha256'), 'hex');
  if p_composite_key <> expected_composite_key then
    raise exception 'announcement_first_seen_validation_composite_invalid';
  end if;

  expected_record_hash := pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.concat_ws(pg_catalog.chr(31),
      p_provider, p_market, p_source_contract_id, p_official_document_id,
      p_official_letter_no, p_entity_id, p_event_type, p_effective_date::text,
      p_source_revision, p_content_hash,
      pg_catalog.to_char(p_first_seen_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
      pg_catalog.coalesce(p_supersedes_content_hash, ''), p_composite_key,
      p_schema_version::text, p_policy_version, p_evidence_mode, p_visibility
    ), 'UTF8'), 'sha256'), 'hex');
  if p_record_hash <> expected_record_hash then
    raise exception 'announcement_first_seen_validation_record_invalid';
  end if;

  return pg_catalog.jsonb_build_object('status', 'valid', 'record_hash', p_record_hash);
end;
$$;

revoke all on function public.validate_announcement_first_seen_payload_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) from public, anon, authenticated, service_role, lineage_observer_owner, lineage_observer_writer;
alter function public.validate_announcement_first_seen_payload_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) owner to lineage_observer_owner;
grant execute on function public.validate_announcement_first_seen_payload_v1(
  text,text,text,text,text,text,text,date,text,text,timestamptz,text,text,text,integer,text,text,text,jsonb
) to lineage_observer_writer;
