do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'reviews'
      and column_name = 'hipaa_review'
  ) then
    alter table public.reviews rename column hipaa_review to compliance_review;
  end if;
end $$;

do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'assessments'
      and column_name = 'hipaa_review'
  ) then
    alter table public.assessments rename column hipaa_review to compliance_review;
  end if;
end $$;
