create table if not exists public.assessments (
  id              bigint generated always as identity primary key,
  repo            text not null,
  summary         text,
  purpose         text,
  tech_stack      jsonb not null default '[]'::jsonb,
  key_components  jsonb not null default '[]'::jsonb,
  vulnerabilities jsonb not null default '[]'::jsonb,
  model           text,
  created_at      timestamptz not null default now()
);

create index if not exists assessments_repo_idx on public.assessments (repo);
create index if not exists assessments_created_at_idx on public.assessments (created_at desc);

alter table public.assessments enable row level security;
