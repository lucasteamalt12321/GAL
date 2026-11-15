-- GAL — 028: видимость доказательств прохождения
-- 028_proof_visibility.sql
--
-- Автор прохождения может скрыть свои доказательства от остальных
-- пользователей. Скрытие действует на уровне прохождения: публичные RPC
-- (карточка достижения и профиль) не отдают proofs, если автор их скрыл.
-- Владелец прохождения и модераторы видят скрытые доказательства: очередь
-- модерации читает proofs напрямую под RLS, а публичные RPC дополнительно
-- проверяют auth.uid()/is_moderator().
--
-- Верификация создателя (прохождение собственного достижения) скрыта быть не
-- может: триггер принудительно выставляет proofs_visible = true. Это защищает
-- инвариант «достижение опубликовано только с публично проверяемым proof
-- создателя» даже при прямом вызове PostgREST.

-- +--------------------------------------------------+
-- | 1. Флаг видимости                                 |
-- +--------------------------------------------------+

alter table public.achievement_completions
    add column if not exists proofs_visible boolean not null default true;

comment on column public.achievement_completions.proofs_visible is
    'Показывать proofs этого прохождения другим пользователям; создатель достижения всегда true.';

create or replace function public.force_creator_proof_visibility()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_creator uuid;
begin
    select a.creator_id
      into v_creator
      from public.achievements a
     where a.id = new.achievement_id;

    if v_creator is not null and new.user_id = v_creator then
        new.proofs_visible := true;
    end if;
    return new;
end;
$$;

drop trigger if exists achievement_completions_creator_visibility
    on public.achievement_completions;
create trigger achievement_completions_creator_visibility
    before insert or update
    on public.achievement_completions
    for each row execute function public.force_creator_proof_visibility();

revoke execute on function public.force_creator_proof_visibility() from public;

-- +--------------------------------------------------+
-- | 2. Прошедшие достижение (для карточки)            |
-- +--------------------------------------------------+

create or replace function public.achievement_completers(p_achievement_id bigint)
returns table (
    user_id uuid,
    username text,
    display_name text,
    avatar_url text,
    approved_at timestamptz,
    proofs jsonb
)
language sql
stable
security definer
set search_path = public
as $$
    select p.id,
           p.username,
           p.display_name,
           p.avatar_url,
           c.approved_at,
           coalesce(
               jsonb_agg(
                   jsonb_build_object(
                       'id', pr.id,
                       'proof_type', pr.proof_type,
                       'description', pr.description,
                       'storage_path', pr.storage_path,
                       'created_at', pr.created_at
                   )
                   order by pr.created_at, pr.id
               ) filter (
                   where pr.id is not null
                     and (
                         pr.proof_type = 'link'
                         or pr.storage_path like c.user_id::text || '/%'
                     )
                     and (
                         c.proofs_visible
                         or c.user_id = auth.uid()
                         or public.is_moderator()
                     )
               ),
               '[]'::jsonb
           )
    from public.achievement_completions c
    join public.achievements a
      on a.id = c.achievement_id
     and a.status = 'published'
    join public.profiles p on p.id = c.user_id
    left join public.proofs pr on pr.completion_id = c.id
    where c.achievement_id = p_achievement_id
      and c.status = 'approved'
    group by p.id, p.username, p.display_name, p.avatar_url, c.approved_at
    order by c.approved_at asc nulls last, p.username asc
    limit 500;
$$;

revoke execute on function public.achievement_completers(bigint) from public;
grant execute on function public.achievement_completers(bigint)
    to anon, authenticated;

-- +--------------------------------------------------+
-- | 3. Пройденные достижения игрока (для профиля)     |
-- +--------------------------------------------------+

create or replace function public.user_completed_achievements(p_user_id uuid)
returns table (
    achievement_id bigint,
    title text,
    rank integer,
    permanent_rank integer,
    ranking_status public.ranking_status,
    bt_rating double precision,
    bt_strength double precision,
    evaluation_count integer,
    category_name text,
    category_slug text,
    approved_at timestamptz,
    proofs jsonb,
    is_temporary boolean
)
language sql
stable
security definer
set search_path = public
as $$
    select a.id,
           a.title,
           a.rank::integer,
           a.permanent_rank::integer,
           a.ranking_status,
           a.bt_rating,
           a.bt_strength,
           a.evaluation_count,
           cat.name,
           cat.slug,
           c.approved_at,
           coalesce(
               jsonb_agg(
                   jsonb_build_object(
                       'id', pr.id,
                       'proof_type', pr.proof_type,
                       'description', pr.description,
                       'storage_path', pr.storage_path,
                       'created_at', pr.created_at
                   )
                   order by pr.created_at, pr.id
               ) filter (
                   where pr.id is not null
                     and (
                         pr.proof_type = 'link'
                         or pr.storage_path like c.user_id::text || '/%'
                     )
                     and (
                         c.proofs_visible
                         or c.user_id = auth.uid()
                         or public.is_moderator()
                     )
               ),
               '[]'::jsonb
           ),
           a.is_temporary
    from public.achievement_completions c
    join public.public_achievements a
      on a.id = c.achievement_id
     and a.status = 'published'
    left join public.categories cat on cat.id = a.category_id
    left join public.proofs pr on pr.completion_id = c.id
    where c.user_id = p_user_id
      and c.status = 'approved'
    group by a.id, a.title, a.rank, a.permanent_rank, a.ranking_status,
             a.bt_rating, a.bt_strength, a.evaluation_count, a.is_temporary,
             cat.name, cat.slug, c.approved_at, c.user_id
    order by a.rank asc nulls last, c.approved_at desc nulls last, a.id asc
    limit 500;
$$;

revoke execute on function public.user_completed_achievements(uuid) from public;
grant execute on function public.user_completed_achievements(uuid)
    to anon, authenticated;

notify pgrst, 'reload schema';
