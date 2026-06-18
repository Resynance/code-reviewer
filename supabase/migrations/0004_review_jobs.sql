-- Async review jobs: the UI enqueues a review and polls for the result, so a
-- slow model call doesn't hold an HTTP request open past the serverless
-- function time limit (which surfaced as a 504).
create table if not exists public.review_jobs (
  id         uuid primary key default gen_random_uuid(),
  status     text not null default 'queued',  -- queued | running | done | error
  request    jsonb not null,                  -- the ReviewRequest payload
  result     jsonb,                           -- the review result when done
  error      text,                            -- the message when status = error
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Same posture as the other app tables: RLS on with no policies, so the table
-- is reachable only via the app's Postgres role, not the Supabase Data API.
alter table public.review_jobs enable row level security;
