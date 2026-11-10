-- GAL — 021: OAuth 2.0 provider (LTHub client)
-- 021_oauth_provider.sql
--
-- Одноразовые authorization-коды и opaque access-токены провайдера GAL.
-- Хранятся только SHA-256-хеши; таблицы доступны исключительно service_role:
-- у anon/authenticated нет политик и грантов.

create table if not exists public.oauth_codes (
    code_hash text primary key,
    client_id text not null,
    profile_id uuid not null references public.profiles (id) on delete cascade,
    redirect_uri text not null,
    code_challenge text not null,
    expires_at bigint not null,
    used_at bigint
);

create index if not exists oauth_codes_expires_idx on public.oauth_codes (expires_at);

create table if not exists public.oauth_tokens (
    token_hash text primary key,
    client_id text not null,
    profile_id uuid not null references public.profiles (id) on delete cascade,
    created_at bigint not null,
    expires_at bigint not null,
    revoked_at bigint
);

create index if not exists oauth_tokens_expires_idx on public.oauth_tokens (expires_at);
create index if not exists oauth_tokens_profile_idx on public.oauth_tokens (profile_id);

alter table public.oauth_codes enable row level security;
alter table public.oauth_tokens enable row level security;

revoke all on public.oauth_codes from anon, authenticated;
revoke all on public.oauth_tokens from anon, authenticated;
grant select, insert, update, delete on public.oauth_codes to service_role;
grant select, insert, update, delete on public.oauth_tokens to service_role;
