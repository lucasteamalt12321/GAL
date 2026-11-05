-- GAL — 013: UI-функции, приватность доказательств и целостность модерации
-- 013_ui_features_and_hardening.sql
--
-- 1. Автор может скрыть (soft-delete) своё достижение, пока оно на модерации.
-- 2. Доказательства подтверждённых выполнений больше не читаются публично:
--    доступ остаётся у владельца и модератора (signed URL в Storage).
-- 3. Записи истории модерации создаются только от имени самого модератора.
-- 4. Публикация достижения невозможна без approved-выполнения создателя
--    с приложенным доказательством (deferred constraint trigger).
-- 5. Постраничный player leaderboard.

-- +--------------------------------------------------+
-- | 1. Soft-delete своего pending-достижения          |
-- +--------------------------------------------------+

drop policy if exists achievements_update_own_pending on public.achievements;
create policy achievements_update_own_pending on public.achievements
    for update
    using (creator_id = auth.uid() and status = 'pending')
    with check (
        creator_id = auth.uid()
        and status in ('pending', 'deleted')
    );

-- +--------------------------------------------------+
-- | 2. Приватность доказательств                      |
-- +--------------------------------------------------+

-- Политика отдавала anon/authenticated строки proofs любого approved
-- completion (включая storage_path и внешние ссылки), хотя UI показывает
-- доказательства только владельцу и модератору. Удаляем.
drop policy if exists proofs_select_approved_public on public.proofs;

-- +--------------------------------------------------+
-- | 3. История модерации — только от своего имени     |
-- +--------------------------------------------------+

drop policy if exists moderation_reviews_insert_moderator on public.moderation_reviews;
create policy moderation_reviews_insert_moderator on public.moderation_reviews
    for insert
    with check (public.is_moderator() and moderator_id = auth.uid());

drop policy if exists reports_update_moderator on public.reports;
create policy reports_update_moderator on public.reports
    for update
    using (public.is_moderator())
    with check (public.is_moderator());

-- +--------------------------------------------------+
-- | 4. Публикация только с proof создателя            |
-- +--------------------------------------------------+

create or replace function public.enforce_creator_proof_on_publish()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.status = 'published'
       and (tg_op = 'INSERT' or old.status is distinct from 'published') then
        if not exists (
            select 1
            from public.achievement_completions c
            join public.proofs p on p.completion_id = c.id
            where c.achievement_id = new.id
              and c.user_id = new.creator_id
              and c.status = 'approved'
        ) then
            raise exception 'PUBLISH_REQUIRES_CREATOR_PROOF'
                using errcode = '23514';
        end if;
    end if;
    return new;
end;
$$;

drop trigger if exists achievements_require_creator_proof on public.achievements;
create constraint trigger achievements_require_creator_proof
    after insert or update of status on public.achievements
    deferrable initially deferred
    for each row execute function public.enforce_creator_proof_on_publish();

revoke execute on function public.enforce_creator_proof_on_publish() from public;

-- +--------------------------------------------------+
-- | 5. Постраничный player leaderboard                |
-- +--------------------------------------------------+

create or replace function public.player_leaderboard_page(
    p_limit integer default 25,
    p_offset integer default 0
)
returns table (
    user_id uuid,
    username text,
    display_name text,
    score numeric,
    achievement_count integer,
    total_count bigint
)
language sql
stable
security definer
set search_path = public
as $$
    with ranked as (
        select p.id as user_id,
               p.username,
               p.display_name,
               round(sum(round((1000.0 / a.rank)::numeric, 2)), 2) as score,
               count(*)::integer as achievement_count
        from public.achievement_completions c
        join public.achievements a
          on a.id = c.achievement_id
         and a.status = 'published'
         and a.ranking_status = 'ranked'
         and a.rank is not null
        join public.profiles p on p.id = c.user_id
        where c.status = 'approved'
        group by p.id, p.username, p.display_name
    )
    select r.*, count(*) over () as total_count
    from ranked r
    order by r.score desc, r.username asc
    limit greatest(p_limit, 1)
    offset greatest(p_offset, 0);
$$;

grant execute on function public.player_leaderboard_page(integer, integer)
    to anon, authenticated;
