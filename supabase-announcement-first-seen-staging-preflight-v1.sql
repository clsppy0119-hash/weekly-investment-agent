-- OFFLINE READ-ONLY FIXTURE. DO NOT AUTO-EXECUTE.
-- A staging administrator may run this only after a separate authority gate.
-- It reads catalog metadata, never application rows or the private ledger.

begin transaction read only;

select pg_catalog.jsonb_build_object(
  'schemaVersion', 1,
  'executorRoleIsPostgres', current_user = 'postgres',
  'transactionReadOnly', pg_catalog.current_setting('transaction_read_only') = 'on',
  'pgcryptoNamespaceExact', exists (
    select 1 from pg_catalog.pg_extension e
    join pg_catalog.pg_namespace n on n.oid = e.extnamespace
    where e.extname = 'pgcrypto' and n.nspname = 'extensions'
  ),
  'digestSignaturePresent', pg_catalog.to_regprocedure('extensions.digest(bytea,text)') is not null,
  'targetRoleCount', (
    select pg_catalog.count(*) from pg_catalog.pg_roles
    where rolname in ('lineage_observer_owner','lineage_observer_writer','lineage_observer_runtime_staging')
  ),
  'targetSchemaCount', (
    select pg_catalog.count(*) from pg_catalog.pg_namespace
    where nspname = 'investment_lineage_admin_v1'
  ),
  'targetRelationCount', (
    select pg_catalog.count(*) from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where (n.nspname, c.relname) in (
      ('public','investment_announcement_first_seen_shadow_v1'),
      ('public','investment_announcement_first_seen_audit_v1'),
      ('investment_lineage_admin_v1','contract_migration_ledger_v1')
    )
  ),
  'targetRoutineCount', (
    select pg_catalog.count(*) from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where (n.nspname, p.proname) in (
      ('public','append_announcement_first_seen_shadow_v1'),
      ('public','validate_announcement_first_seen_payload_v1'),
      ('public','reject_announcement_first_seen_mutation_v1')
    )
  ),
  'authenticatorOverrideKnown', pg_catalog.current_setting('pgrst.db_schemas', true) is not null,
  'privateSchemaInAuthenticatorOverride', pg_catalog.position(
    'investment_lineage_admin_v1' in pg_catalog.coalesce(
      pg_catalog.current_setting('pgrst.db_schemas', true), ''
    )
  ) > 0,
  'privateRuntimeGrantCount', (
    select pg_catalog.count(*) from information_schema.usage_privileges
    where object_schema = 'investment_lineage_admin_v1'
      and grantee in ('PUBLIC','anon','authenticated','service_role',
                      'lineage_observer_owner','lineage_observer_writer',
                      'lineage_observer_runtime_staging')
  ),
  'privateViewExposureCount', (
    select pg_catalog.count(*) from pg_catalog.pg_views
    where definition ilike '%investment_lineage_admin_v1%'
  ) + (
    select pg_catalog.count(*) from pg_catalog.pg_matviews
    where definition ilike '%investment_lineage_admin_v1%'
  ),
  'privateRoutineExposureCount', (
    select pg_catalog.count(*) from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('public','graphql_public','storage')
      and pg_catalog.pg_get_functiondef(p.oid) ilike '%investment_lineage_admin_v1%'
  ),
  'privatePublicationExposureCount', (
    select pg_catalog.count(*) from pg_catalog.pg_publication_tables
    where schemaname = 'investment_lineage_admin_v1'
  )
) as staging_preflight_summary;

commit;
