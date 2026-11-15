-- GAL — 026: доказательства в публичных списках прохождений
-- 026_public_completion_proofs.sql
--
-- Публичные страницы показывают, кто прошёл достижение и что прошёл игрок,
-- но без самих доказательств. Расширяем RPC из 023: к каждому approved-
-- прохождению опубликованного достижения добавляем proofs (id, тип,
-- описание, storage_path, дата). Ничего, кроме доказательств, не раскрывается:
-- pending/rejected, email и moderation_reviews по-прежнему недоступны.
--
-- storage_path для link-доказательства — внешний URL (приложение проверяет
-- http/https), для файла — путь в приватном бакете; ссылку на файл приложение
-- подписывает через service role, потому что Storage RLS не пускает anon.
-- Файл берём только из папки своего пользователя: иначе строка proofs с чужим
-- storage_path позволила бы подписать чужое доказательство.

-- +--------------------------------------------------+
-- | 1. Прошедшие достижение (для карточки)            |
-- +--------------------------------------------------+

drop function if exists public.achievement_completers(bigint);

create function public.achievement_completers(p_achievement_id bigint)
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
-- | 2. Пройденные достижения игрока (для профиля)     |
-- +--------------------------------------------------+

drop function if exists public.user_completed_achievements(uuid);

create function public.user_completed_achievements(p_user_id uuid)
returns table (
    achievement_id bigint,
    title text,
    rank integer,
    ranking_status public.ranking_status,
    bt_rating double precision,
    bt_strength double precision,
    evaluation_count integer,
    category_name text,
    category_slug text,
    approved_at timestamptz,
    proofs jsonb
)
language sql
stable
security definer
set search_path = public
as $$
    select a.id,
           a.title,
           a.rank,
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
               ),
               '[]'::jsonb
           )
    from public.achievement_completions c
    join public.achievements a
      on a.id = c.achievement_id
     and a.status = 'published'
    left join public.categories cat on cat.id = a.category_id
    left join public.proofs pr on pr.completion_id = c.id
    where c.user_id = p_user_id
      and c.status = 'approved'
    group by a.id, a.title, a.rank, a.ranking_status, a.bt_rating,
             a.bt_strength, a.evaluation_count, cat.name, cat.slug,
             c.approved_at
    order by a.rank asc nulls last, c.approved_at desc nulls last, a.id asc
    limit 500;
$$;

revoke execute on function public.user_completed_achievements(uuid) from public;
grant execute on function public.user_completed_achievements(uuid)
    to anon, authenticated;
