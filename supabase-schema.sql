create table if not exists public.user_portfolios(user_id uuid primary key references auth.users(id) on delete cascade,portfolio jsonb not null default '{}'::jsonb,updated_at timestamptz not null default now());
alter table public.user_portfolios enable row level security;
create policy "read own portfolio" on public.user_portfolios for select to authenticated using ((select auth.uid())=user_id);
create policy "insert own portfolio" on public.user_portfolios for insert to authenticated with check ((select auth.uid())=user_id);
create policy "update own portfolio" on public.user_portfolios for update to authenticated using ((select auth.uid())=user_id) with check ((select auth.uid())=user_id);
create policy "delete own portfolio" on public.user_portfolios for delete to authenticated using ((select auth.uid())=user_id);
