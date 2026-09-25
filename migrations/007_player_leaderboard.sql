-- GAL — player leaderboard aggregation
-- 007_player_leaderboard.sql
-- Игроки видят лидерборд публично; RLS закрывает achievement_completions
-- (только свои/модератор), поэтому агрегированный счёт отдаёт security definer
-- функция: доход = 1000 / rank по каждому подтверждённому (approved) достижению
-- со статусом ranked+published. unknown достижения дают 0 очков (Score policy MVP).

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
    order by score desc, p.username asc;
$$;

grant execute on function public.player_leaderboard() to anon, authenticated;