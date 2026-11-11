-- GAL — 023: публичные списки прошедших
-- 023_public_completions.sql
--
-- RLS на achievement_completions разрешает читать только свои заявки и
-- модераторам, поэтому публичные страницы не могли показать:
--   1. кто прошёл достижение (карточка достижения);
--   2. какие достижения прошёл игрок (публичный профиль).
-- Обе функции — security definer и возвращают только approved-заявки
-- опубликованных достижений и только публичные поля профиля. Proofs,
-- email, pending/rejected и moderation_reviews наружу не попадают.

-- +--------------------------------------------------+
-- | 1. Прошедшие достижение (для карточки)            |
-- +--------------------------------------------------+

create or replace function public.achievement_completers(p_achievement_id bigint)
returns table (
    user_id uuid,
    username text,
    display_name text,
    avatar_url text,
    approved_at timestamptz
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
           c.approved_at
    from public.achievement_completions c
    join public.achievements a
      on a.id = c.achievement_id
     and a.status = 'published'
    join public.profiles p on p.id = c.user_id
    where c.achievement_id = p_achievement_id
      and c.status = 'approved'
    order by c.approved_at asc nulls last, p.username asc
    limit 500;
$$;

revoke execute on function public.achievement_completers(bigint) from public;
grant execute on function public.achievement_completers(bigint)
    to anon, authenticated;

-- +--------------------------------------------------+
-- | 2. Пройденные достижения игрока (для профиля)     |
-- +--------------------------------------------------+

create or replace function public.user_completed_achievements(p_user_id uuid)
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
    approved_at timestamptz
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
           c.approved_at
    from public.achievement_completions c
    join public.achievements a
      on a.id = c.achievement_id
     and a.status = 'published'
    left join public.categories cat on cat.id = a.category_id
    where c.user_id = p_user_id
      and c.status = 'approved'
    order by a.rank asc nulls last, c.approved_at desc nulls last, a.id asc
    limit 500;
$$;

revoke execute on function public.user_completed_achievements(uuid) from public;
grant execute on function public.user_completed_achievements(uuid)
    to anon, authenticated;
