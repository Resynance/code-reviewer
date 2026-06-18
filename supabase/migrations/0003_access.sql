-- Access allowlist: which signed-in users may use the app. Editable at runtime
-- (no redeploy). ALLOWED_EMAILS env still works as an optional bootstrap/fallback.

create table if not exists public.access_allowlist (
  email      text primary key,
  created_at timestamptz not null default now()
);

-- App connects as the Postgres role (bypasses RLS); RLS-on + no policies keeps
-- this table off the Data API.
alter table public.access_allowlist enable row level security;
