-- ReviewBot schema for Supabase Postgres.
-- Run once in the Supabase SQL editor (or via `supabase db push`).
--
-- NOTE: the vector(N) dimension MUST match EMBEDDING_DIM / the embedding model
-- (default 1536 for openai/text-embedding-3-small). If you change the embedding
-- model to one with a different dimension, change 1536 here to match and
-- re-backfill.

-- pgvector (you can also enable this from Dashboard → Database → Extensions).
create extension if not exists vector with schema extensions;

-- Supabase installs pgvector in the extensions schema; make sure the type is
-- visible without a schema prefix for the rest of this migration.
set search_path to public, extensions;

-- Decisions: one row per past PR / ADR, with its embedding for semantic search.
create table if not exists public.decisions (
  doc_id    text primary key,
  metadata  jsonb not null,
  embedding vector(1536) not null
);

-- Approximate nearest-neighbour index for cosine similarity (<=> operator).
create index if not exists decisions_embedding_idx
  on public.decisions using hnsw (embedding vector_cosine_ops);

-- Speeds up the per-repo / global scope filter (metadata->>'repo').
create index if not exists decisions_repo_idx
  on public.decisions ((metadata->>'repo'));

-- App settings: a single JSONB blob (github token, webhook secret, repos, models).
create table if not exists public.app_settings (
  id   int primary key default 1,
  data jsonb not null default '{}'::jsonb,
  constraint app_settings_singleton check (id = 1)
);

-- The app connects as the Postgres role over DATABASE_URL (direct), which
-- bypasses RLS. Enable RLS with NO policies so these tables are NOT reachable
-- through the Supabase Data API (PostgREST) by anon/authenticated roles.
alter table public.decisions enable row level security;
alter table public.app_settings enable row level security;
