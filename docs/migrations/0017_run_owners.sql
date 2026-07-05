-- 0017_run_owners.sql — per-user ownership of Hermes runs, so a member keeps
-- access to their own in-flight runs across an orchestrator restart (idempotency
-- Phase 5). Mirrors 0011_agent_sessions.sql. Apply via the Supabase dashboard
-- (project kpdqhntsvjzhqjeupzsj, SQL Editor); mirror into
-- jnow-workspace/development/core/supabase/migrations/ for the canonical record.
-- NOTE: confirm 0017 is the next free number against the live project before applying.

create table if not exists public.run_owners (
  run_id      text primary key,          -- Hermes run id (globally-unique uuid)
  user_id     uuid not null,             -- Supabase auth.users id (JWT `sub`)
  created_at  timestamptz not null default now()
);

create index if not exists run_owners_user_idx on public.run_owners (user_id);

alter table public.run_owners enable row level security;

-- SELECT own rows only (defense in depth; the orchestrator reads via the service
-- role, which bypasses RLS). No write policies -> only the service role writes.
create policy run_owners_select_own on public.run_owners
  for select to authenticated
  using (user_id = auth.uid());

comment on table public.run_owners is
  'Idempotency Phase 5: maps Hermes run ids to owning users so the orchestrator run gate survives a restart; enforced fail-closed.';
