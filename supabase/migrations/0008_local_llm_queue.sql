-- Add execution metadata so LLM jobs can be claimed by an external local worker.
alter table public.review_jobs
  add column if not exists job_type text not null default 'review',
  add column if not exists executor text not null default 'inline',
  add column if not exists claimed_by text,
  add column if not exists started_at timestamptz,
  add column if not exists completed_at timestamptz;

create index if not exists review_jobs_queue_idx
  on public.review_jobs (executor, status, created_at);
