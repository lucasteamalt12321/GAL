begin;

-- Очки игрока зависят от BT-рейтинга достижения, а не от его места.
-- Лучшее опубликованное ранжированное достижение даёт 1000 очков;
-- разница в 400 рейтинга соответствует десятикратной разнице в очках.

create or replace function public.player_leaderboard_scores()
returns table (
    user_id uuid,
    username text,
    display_name text,
    score numeric,
    achievement_count integer
)
language sql
stable
security definer
set search_path = public
as $$
    with eligible as (
        select a.id,
               a.bt_rating,
               max(a.bt_rating) over () as max_bt_rating
        from public.achievements a
        where a.status = 'published'
          and a.ranking_status = 'ranked'
          and a.rank is not null
          and a.bt_rating is not null
    ),
    scored as (
        select c.user_id,
               round(
                   (1000.0 * power(10.0, (e.bt_rating - e.max_bt_rating) / 400.0))::numeric,
                   2
               ) as points
        from public.achievement_completions c
        join eligible e on e.id = c.achievement_id
        where c.status = 'approved'
    )
    select p.id,
           p.username,
           p.display_name,
           round(sum(s.points), 2),
           count(*)::integer
    from scored s
    join public.profiles p on p.id = s.user_id
    group by p.id, p.username, p.display_name
    order by round(sum(s.points), 2) desc, p.username asc;
$$;

revoke all on function public.player_leaderboard_scores()
    from public, anon, authenticated, service_role;

create or replace function public.player_leaderboard()
returns table (
    user_id uuid,
    username text,
    display_name text,
    score numeric,
    achievement_count integer
)
language sql
stable
security definer
set search_path = public
as $$
    select s.*
    from public.player_leaderboard_scores() s
    order by s.score desc, s.username asc;
$$;

revoke execute on function public.player_leaderboard() from public, anon, authenticated;
grant execute on function public.player_leaderboard() to anon, authenticated;

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
    select s.*, count(*) over () as total_count
    from public.player_leaderboard_scores() s
    order by s.score desc, s.username asc
    limit greatest(p_limit, 1)
    offset greatest(p_offset, 0);
$$;

revoke execute on function public.player_leaderboard_page(integer, integer)
    from public, anon, authenticated;
grant execute on function public.player_leaderboard_page(integer, integer)
    to anon, authenticated;

create or replace function public.player_leaderboard_search_page(
    p_limit integer default 25,
    p_offset integer default 0,
    p_query text default ''
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
    with filtered as (
        select s.*
        from public.player_leaderboard_scores() s
        where nullif(btrim(p_query), '') is null
           or strpos(lower(s.username), lower(btrim(p_query))) > 0
           or strpos(lower(coalesce(s.display_name, '')), lower(btrim(p_query))) > 0
    )
    select f.*, count(*) over () as total_count
    from filtered f
    order by f.score desc, f.username asc
    limit least(greatest(p_limit, 1), 100)
    offset greatest(p_offset, 0);
$$;

revoke execute on function public.player_leaderboard_search_page(integer, integer, text)
    from public, anon, authenticated;
grant execute on function public.player_leaderboard_search_page(integer, integer, text)
    to anon, authenticated;

notify pgrst, 'reload schema';

commit;
