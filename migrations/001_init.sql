-- GAL — initial schema
-- 001_init.sql

-- +---------------------------+
-- | Enumerated types          |
-- +---------------------------+

create type public.user_role as enum ('user', 'moderator', 'admin');

create type public.achievement_status as enum ('pending', 'published', 'rejected', 'deleted');

create type public.ranking_status as enum ('unknown', 'ranked');

create type public.completion_status as enum ('pending', 'approved', 'rejected');

create type public.proof_type as enum ('video', 'image', 'link', 'document', 'other');

create type public.review_decision as enum ('approved', 'rejected');

create type public.report_status as enum ('pending', 'accepted', 'rejected');

-- +---------------------------+
-- | profiles                  |
-- +---------------------------+

create table if not exists public.profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    username text not null,
    display_name text,
    avatar_url text,
    role public.user_role not null default 'user',
    created_at timestamptz not null default now()
);

create unique index if not exists profiles_username_unique on public.profiles (lower(username));

create index if not exists profiles_role_idx on public.profiles (role);

-- +---------------------------+
-- | categories                |
-- +---------------------------+

create table if not exists public.categories (
    id bigint generated always as identity primary key,
    name text not null,
    slug text not null,
    created_at timestamptz not null default now()
);

create unique index if not exists categories_name_unique on public.categories (name);
create unique index if not exists categories_slug_unique on public.categories (slug);

insert into public.categories (name, slug)
values
    ('Geometry Dash', 'geometry-dash'),
    ('Music', 'music'),
    ('Piano', 'piano'),
    ('Chess', 'chess'),
    ('Programming', 'programming'),
    ('Minecraft', 'minecraft'),
    ('Education', 'education'),
    ('Science', 'science'),
    ('Other', 'other')
on conflict (name) do nothing;

-- +---------------------------+
-- | achievements              |
-- +---------------------------+

create table if not exists public.achievements (
    id bigint generated always as identity primary key,
    title text not null,
    description text not null default '',
    requirements text not null default '',
    category_id bigint references public.categories (id),
    creator_id uuid not null references public.profiles (id),
    status public.achievement_status not null default 'pending',
    ranking_status public.ranking_status not null default 'unknown',
    average_position double precision,
    rank integer,
    evaluation_count integer not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint achievements_evaluation_count_non_negative check (evaluation_count >= 0),
    constraint achievements_rank_requires_ranked check (
        (ranking_status = 'ranked' and rank is not null and average_position is not null)
        or (ranking_status = 'unknown' and rank is null)
    )
);

create index if not exists achievements_status_idx on public.achievements (status);
create index if not exists achievements_category_idx on public.achievements (category_id);
create index if not exists achievements_creator_idx on public.achievements (creator_id);
create index if not exists achievements_ranked_idx on public.achievements (status, rank) where status = 'published';
create unique index if not exists achievements_rank_unique on public.achievements (rank) where rank is not null;

-- +---------------------------+
-- | achievement_completions   |
-- +---------------------------+

create table if not exists public.achievement_completions (
    id bigint generated always as identity primary key,
    achievement_id bigint not null references public.achievements (id),
    user_id uuid not null references public.profiles (id),
    status public.completion_status not null default 'pending',
    created_at timestamptz not null default now(),
    approved_at timestamptz,
    constraint achievement_completions_unique unique (achievement_id, user_id)
);

create index if not exists achievement_completions_user_idx on public.achievement_completions (user_id, status);
create index if not exists achievement_completions_status_idx on public.achievement_completions (status);

-- +---------------------------+
-- | proofs                    |
-- +---------------------------+

create table if not exists public.proofs (
    id bigint generated always as identity primary key,
    completion_id bigint not null references public.achievement_completions (id) on delete cascade,
    storage_path text not null,
    proof_type public.proof_type not null default 'other',
    description text not null default '',
    created_at timestamptz not null default now()
);

create index if not exists proofs_completion_idx on public.proofs (completion_id);

-- +---------------------------+
-- | evaluations               |
-- +---------------------------+

create table if not exists public.evaluations (
    id bigint generated always as identity primary key,
    achievement_id bigint not null references public.achievements (id),
    user_id uuid not null references public.profiles (id),
    position double precision not null,
    locked boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint evaluations_unique unique (achievement_id, user_id),
    constraint evaluations_position_positive check (position >= 1)
);

create index if not exists evaluations_achievement_idx on public.evaluations (achievement_id);
create index if not exists evaluations_user_idx on public.evaluations (user_id);

-- +---------------------------+
-- | moderation_reviews        |
-- +---------------------------+

create table if not exists public.moderation_reviews (
    id bigint generated always as identity primary key,
    completion_id bigint references public.achievement_completions (id),
    achievement_id bigint references public.achievements (id),
    moderator_id uuid not null references public.profiles (id),
    decision public.review_decision not null,
    reason text not null default '',
    created_at timestamptz not null default now(),
    constraint moderation_reviews_target_present check (
        num_nonnulls(completion_id, achievement_id) >= 1
    )
);

create index if not exists moderation_reviews_completion_idx on public.moderation_reviews (completion_id);
create index if not exists moderation_reviews_achievement_idx on public.moderation_reviews (achievement_id);
create index if not exists moderation_reviews_moderator_idx on public.moderation_reviews (moderator_id);

-- +---------------------------+
-- | reports                   |
-- +---------------------------+

create table if not exists public.reports (
    id bigint generated always as identity primary key,
    achievement_id bigint not null references public.achievements (id),
    reporter_id uuid not null references public.profiles (id),
    reason text not null,
    description text not null default '',
    status public.report_status not null default 'pending',
    created_at timestamptz not null default now(),
    resolved_at timestamptz,
    resolved_by uuid references public.profiles (id),
    resolution_reason text
);

create index if not exists reports_achievement_idx on public.reports (achievement_id);
create index if not exists reports_reporter_idx on public.reports (reporter_id);
create index if not exists reports_status_idx on public.reports (status);

-- +---------------------------+
-- | triggers                  |
-- +---------------------------+

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create or replace trigger achievements_set_updated_at
    before update on public.achievements
    for each row execute procedure public.set_updated_at();

create or replace trigger evaluations_set_updated_at
    before update on public.evaluations
    for each row execute procedure public.set_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    insert into public.profiles (id, username, display_name)
    values (
        new.id,
        coalesce(
            new.raw_user_meta_data ->> 'username',
            'user_' || substr(replace(new.id::text, '-', ''), 1, 12)
        ),
        nullif(new.raw_user_meta_data ->> 'display_name', '')
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

create or replace trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();