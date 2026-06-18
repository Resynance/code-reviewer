-- Review history: one row per review run (full history, append-only).
-- Run once in the Supabase SQL editor (or via `supabase db push`).

create table if not exists public.reviews (
  id             bigint generated always as identity primary key,
  repo           text not null,
  pr_number      integer not null,
  title          text,
  author         text,
  approved       boolean,
  confidence     double precision,
  summary        text,
  issues         jsonb not null default '[]'::jsonb,
  suggestions    jsonb not null default '[]'::jsonb,
  past_decisions jsonb not null default '[]'::jsonb,
  source         text,                       -- 'api' | 'webhook'
  created_at     timestamptz not null default now()
);

-- Browse history newest-first, filterable by repo / PR.
create index if not exists reviews_repo_pr_idx
  on public.reviews (repo, pr_number, created_at desc);
create index if not exists reviews_created_idx
  on public.reviews (created_at desc);

-- App connects as the Postgres role (bypasses RLS); RLS-on + no policies keeps
-- this table off the Data API.
alter table public.reviews enable row level security;
