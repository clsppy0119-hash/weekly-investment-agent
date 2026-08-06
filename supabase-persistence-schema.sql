-- Durable, append/upsert-only market history.  Raw rows stay private to the
-- database; the public website never receives the service-role key.
create table if not exists public.investment_market_daily (
  stock_id text not null,
  trading_date date not null,
  close numeric not null,
  volume numeric,
  source text not null default 'finmind',
  raw jsonb not null default '{}'::jsonb,
  ingested_at timestamptz not null default now(),
  primary key (stock_id, trading_date)
);
create index if not exists investment_market_daily_date_idx on public.investment_market_daily (trading_date);

create table if not exists public.investment_corporate_actions (
  stock_id text not null,
  event_date date not null,
  event_type text not null,
  payload jsonb not null,
  ingested_at timestamptz not null default now(),
  primary key (stock_id, event_date, event_type)
);

create table if not exists public.investment_data_sync_runs (
  run_id uuid primary key default gen_random_uuid(),
  source text not null,
  rows_seen integer not null default 0,
  rows_written integer not null default 0,
  status text not null,
  details jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

-- Shadow-only, append-only lineage. It contains metadata hashes only, never
-- raw rows, credentials, or browser-readable pointers.
create table if not exists public.investment_data_lineage_shadow (
  provider text not null,
  dataset text not null,
  entity_id text not null,
  observation_period text not null,
  source_revision text not null,
  available_at timestamptz not null,
  schema_version integer not null,
  content_hash text not null,
  composite_key text not null,
  supersedes_content_hash text,
  metadata jsonb not null,
  ingested_at timestamptz not null default now(),
  primary key (provider, dataset, entity_id, observation_period, source_revision, available_at, schema_version, content_hash),
  unique (composite_key, content_hash)
);

alter table public.investment_market_daily enable row level security;
alter table public.investment_corporate_actions enable row level security;
alter table public.investment_data_sync_runs enable row level security;
alter table public.investment_data_lineage_shadow enable row level security;
drop policy if exists "authenticated can read market history" on public.investment_market_daily;
create policy "authenticated can read market history" on public.investment_market_daily for select to authenticated using (true);
drop policy if exists "authenticated can read corporate actions" on public.investment_corporate_actions;
create policy "authenticated can read corporate actions" on public.investment_corporate_actions for select to authenticated using (true);
drop policy if exists "authenticated can read sync runs" on public.investment_data_sync_runs;
create policy "authenticated can read sync runs" on public.investment_data_sync_runs for select to authenticated using (true);
revoke insert, update, delete on public.investment_market_daily from anon, authenticated;
revoke insert, update, delete on public.investment_corporate_actions from anon, authenticated;
revoke insert, update, delete on public.investment_data_sync_runs from anon, authenticated;
revoke all on public.investment_data_lineage_shadow from anon, authenticated;
