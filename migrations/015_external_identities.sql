-- GAL — 015: внешние identity (вход через LTHub)
-- 015_external_identities.sql
--
-- LTHub OAuth связывает внешний subject с Supabase-профилем. Таблица доступна
-- только service_role: у anon/authenticated нет политик и грантов, поэтому
-- прямые REST-запросы не видят привязки.

create table if not exists public.external_identities (
    id bigint generated always as identity primary key,
    provider text not null,
    subject text not null,
    profile_id uuid not null references public.profiles (id) on delete cascade,
    created_at timestamptz not null default now(),
    constraint external_identities_provider_subject_key unique (provider, subject),
    constraint external_identities_provider_profile_key unique (provider, profile_id)
);

create index if not exists external_identities_profile_idx
    on public.external_identities (profile_id);

alter table public.external_identities enable row level security;

revoke all on public.external_identities from anon, authenticated;
grant select, insert, update, delete on public.external_identities to service_role;
grant usage, select on all sequences in schema public to service_role;
