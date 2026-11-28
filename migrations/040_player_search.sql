begin;

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
    ),
    filtered as (
        select r.*
        from ranked r
        where nullif(btrim(p_query), '') is null
           or strpos(lower(r.username), lower(btrim(p_query))) > 0
           or strpos(lower(coalesce(r.display_name, '')), lower(btrim(p_query))) > 0
    )
    select f.*, count(*) over () as total_count
    from filtered f
    order by f.score desc, f.username asc
    limit least(greatest(p_limit, 1), 100)
    offset greatest(p_offset, 0);
$$;

revoke execute on function public.player_leaderboard_search_page(
    integer, integer, text
) from public, anon, authenticated;
grant execute on function public.player_leaderboard_search_page(
    integer, integer, text
) to anon, authenticated;

notify pgrst, 'reload schema';

commit;
