-- GAL — Row Level Security
-- 002_rls.sql
-- Политики по §47 Master Prompt. Role-назначения и привилегированные операции
-- выполняются только через service role (обход RLS осознанно).

-- +---------------------------+
-- | Helper functions по ролям |
-- +---------------------------+

create or replace function public.is_moderator()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.profiles p
        where p.id = auth.uid()
          and p.role in ('moderator', 'admin')
    )
$$;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.profiles p
        where p.id = auth.uid()
          and p.role = 'admin'
    )
$$;

-- +---------------------------+
-- | Защита роли от изменения  |
-- +---------------------------+

create or replace function public.prevent_profile_role_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.role is distinct from old.role then
        if coalesce(
            nullif(current_setting('request.jwt.claims', true), '')::json ->> 'role',
            ''
        ) <> 'service_role' then
            raise exception 'Only administrators can change user roles';
        end if;
    end if;
    return new;
end;
$$;

drop trigger if exists prevent_profile_role_change_trigger on public.profiles;

create trigger prevent_profile_role_change_trigger
    before update on public.profiles
    for each row execute procedure public.prevent_profile_role_change();

-- +---------------------------+
-- | Включение RLS             |
-- +---------------------------+

alter table public.profiles enable row level security;
alter table public.categories enable row level security;
alter table public.achievements enable row level security;
alter table public.achievement_completions enable row level security;
alter table public.proofs enable row level security;
alter table public.evaluations enable row level security;
alter table public.moderation_reviews enable row level security;
alter table public.reports enable row level security;

-- +---------------------------+
-- | profiles                  |
-- +---------------------------+

drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles
    for select
    using (true);

drop policy if exists profiles_insert_own on public.profiles;
create policy profiles_insert_own on public.profiles
    for insert
    with check (id = auth.uid());

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
    for update
    using (id = auth.uid())
    with check (id = auth.uid());

-- +---------------------------+
-- | categories                |
-- +---------------------------+

drop policy if exists categories_select on public.categories;
create policy categories_select on public.categories
    for select
    using (true);

-- +---------------------------+
-- | achievements              |
-- +---------------------------+

drop policy if exists achievements_select_public on public.achievements;
create policy achievements_select_public on public.achievements
    for select
    using (status = 'published');

drop policy if exists achievements_select_own on public.achievements;
create policy achievements_select_own on public.achievements
    for select
    using (creator_id = auth.uid());

drop policy if exists achievements_select_moderator on public.achievements;
create policy achievements_select_moderator on public.achievements
    for select
    using (public.is_moderator());

drop policy if exists achievements_insert_own on public.achievements;
create policy achievements_insert_own on public.achievements
    for insert
    with check (creator_id = auth.uid() and status = 'pending');

drop policy if exists achievements_update_own_pending on public.achievements;
create policy achievements_update_own_pending on public.achievements
    for update
    using (creator_id = auth.uid() and status = 'pending')
    with check (creator_id = auth.uid() and status = 'pending');

drop policy if exists achievements_update_moderator on public.achievements;
create policy achievements_update_moderator on public.achievements
    for update
    using (public.is_moderator());

-- +---------------------------+
-- | achievement_completions   |
-- +---------------------------+

drop policy if exists completions_insert_own on public.achievement_completions;
create policy completions_insert_own on public.achievement_completions
    for insert
    with check (user_id = auth.uid() and status = 'pending');

drop policy if exists completions_select_own on public.achievement_completions;
create policy completions_select_own on public.achievement_completions
    for select
    using (user_id = auth.uid());

drop policy if exists completions_select_moderator on public.achievement_completions;
create policy completions_select_moderator on public.achievement_completions
    for select
    using (public.is_moderator());

drop policy if exists completions_update_moderator on public.achievement_completions;
create policy completions_update_moderator on public.achievement_completions
    for update
    using (public.is_moderator());

-- +---------------------------+
-- | proofs                    |
-- +---------------------------+

drop policy if exists proofs_insert_own on public.proofs;
create policy proofs_insert_own on public.proofs
    for insert
    with check (
        auth.uid() = (
            select c.user_id
            from public.achievement_completions c
            where c.id = proofs.completion_id
        )
    );

drop policy if exists proofs_select_own on public.proofs;
create policy proofs_select_own on public.proofs
    for select
    using (
        auth.uid() = (
            select c.user_id
            from public.achievement_completions c
            where c.id = proofs.completion_id
        )
    );

drop policy if exists proofs_select_moderator on public.proofs;
create policy proofs_select_moderator on public.proofs
    for select
    using (public.is_moderator());

drop policy if exists proofs_select_approved_public on public.proofs;
create policy proofs_select_approved_public on public.proofs
    for select
    using (
        exists (
            select 1
            from public.achievement_completions c
            where c.id = proofs.completion_id
              and c.status = 'approved'
        )
    );

-- +---------------------------+
-- | evaluations               |
-- +---------------------------+

drop policy if exists evaluations_select_own on public.evaluations;
create policy evaluations_select_own on public.evaluations
    for select
    using (user_id = auth.uid());

drop policy if exists evaluations_select_moderator on public.evaluations;
create policy evaluations_select_moderator on public.evaluations
    for select
    using (public.is_moderator());

drop policy if exists evaluations_insert_own on public.evaluations;
create policy evaluations_insert_own on public.evaluations
    for insert
    with check (
        user_id = auth.uid()
        and exists (
            select 1
            from public.achievement_completions c
            where c.achievement_id = evaluations.achievement_id
              and c.user_id = auth.uid()
              and c.status = 'approved'
        )
    );

drop policy if exists evaluations_update_own_unlocked on public.evaluations;
create policy evaluations_update_own_unlocked on public.evaluations
    for update
    using (user_id = auth.uid() and locked = false)
    with check (user_id = auth.uid() and locked = false);

-- +---------------------------+
-- | moderation_reviews        |
-- +---------------------------+

drop policy if exists moderation_reviews_insert_moderator on public.moderation_reviews;
create policy moderation_reviews_insert_moderator on public.moderation_reviews
    for insert
    with check (public.is_moderator());

drop policy if exists moderation_reviews_select_moderator on public.moderation_reviews;
create policy moderation_reviews_select_moderator on public.moderation_reviews
    for select
    using (public.is_moderator());

-- +---------------------------+
-- | reports                   |
-- +---------------------------+

drop policy if exists reports_insert_own on public.reports;
create policy reports_insert_own on public.reports
    for insert
    with check (reporter_id = auth.uid() and status = 'pending');

drop policy if exists reports_select_own on public.reports;
create policy reports_select_own on public.reports
    for select
    using (reporter_id = auth.uid());

drop policy if exists reports_select_moderator on public.reports;
create policy reports_select_moderator on public.reports
    for select
    using (public.is_moderator());

drop policy if exists reports_update_moderator on public.reports;
create policy reports_update_moderator on public.reports
    for update
    using (public.is_moderator());

-- +---------------------------+
-- | Grants                    |
-- +---------------------------+

grant usage on schema public to anon, authenticated;

grant select on public.categories to anon, authenticated;

grant select on public.profiles to anon, authenticated;
grant update on public.profiles to authenticated;

grant select on public.achievements to anon, authenticated;
grant select, insert, update on public.achievements to authenticated;

grant select, insert, update on public.achievement_completions to authenticated;

grant select on public.proofs to anon, authenticated;
grant select, insert on public.proofs to authenticated;

grant select, insert, update on public.evaluations to authenticated;

grant select, insert on public.moderation_reviews to authenticated;

grant select, insert, update on public.reports to authenticated;

grant all on all tables in schema public to service_role;