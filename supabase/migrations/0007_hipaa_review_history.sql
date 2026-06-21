alter table if exists public.reviews
  add column if not exists hipaa_review jsonb not null default '{}'::jsonb;

alter table if exists public.assessments
  add column if not exists hipaa_review jsonb not null default '{}'::jsonb;
