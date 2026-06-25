create table if not exists public.compliance_analysis (
  id          bigint generated always as identity primary key,
  repo        text not null,
  health      jsonb not null default '{}'::jsonb,
  coverage    jsonb not null default '{}'::jsonb,
  suggestions jsonb not null default '[]'::jsonb,
  created_at  timestamptz not null default now()
);

create index if not exists compliance_analysis_repo_idx on public.compliance_analysis (repo);
create index if not exists compliance_analysis_created_at_idx on public.compliance_analysis (created_at desc);

alter table public.compliance_analysis enable row level security;
