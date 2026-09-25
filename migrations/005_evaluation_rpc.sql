-- GAL — evaluation engine
-- 005_evaluation_rpc.sql
-- Личная шкала сложности: позиция = целочисленный индекс в личной шкале
-- пользователя (1 = самое сложное из сделанного). Новая оценка вставляется
-- по числу уже оценённых достижений, которые ОБЩЕ сложнее (p_harder_count):
--   new_position = p_harder_count + 1, существующие позиции сдвигаются.
-- При достижении >= 3 оценок достижение переходит в 'ranked', оценки
-- фиксируются (locked = true) и выполняется пересчёт мирового ранга.
-- Всё выполняется атомарно в security definer функции.

alter table if exists public.achievements
    add column if not exists rank_order double precision;

-- Значения для уже ранжированных (создаются только после этого миграциями).
update public.achievements
set rank_order = random()
where ranking_status = 'ranked' and rank_order is null;

-- Пересчёт мирового ранга: критерий — сначала средняя позиция (меньше = сложнее),
-- затем фиксированный random ти-брейкер из момента перехода в 'ranked'.
create or replace function public.recompute_ranks()
returns void
language sql
security definer
set search_path = public
as $$
    with ranked as (
        select id,
               row_number() over (
                   order by average_position asc, rank_order asc, id asc
               ) as new_rank
        from public.achievements
        where ranking_status = 'ranked'
          and status = 'published'
    )
    update public.achievements a
    set rank = r.new_rank
    from ranked r
    where a.id = r.id;
$$;

create or replace function public.submit_evaluation(
    p_achievement_id bigint,
    p_harder_count integer
)
returns table (
    achievement_id bigint,
    evaluation_count integer,
    average_position double precision,
    ranking_status public.ranking_status,
    my_position double precision
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_approved boolean;
    v_row evaluations%rowtype;
    v_old_pos double precision;
    v_max_pos double precision;
    v_new_pos double precision;
    v_count integer;
    v_avg double precision;
begin
    if v_uid is null then
        raise exception 'AUTH_REQUIRED';
    end if;

    select exists (
        select 1
        from public.achievement_completions c
        join public.achievements a on a.id = c.achievement_id
                                   and a.status = 'published'
        where c.user_id = v_uid
          and c.achievement_id = p_achievement_id
          and c.status = 'approved'
    ) into v_approved;

    if not v_approved then
        raise exception 'NOT_APPROVED';
    end if;

    select *
    into v_row
    from public.evaluations e
    where e.achievement_id = p_achievement_id
      and e.user_id = v_uid;

    if v_row.id is not null and v_row.locked then
        raise exception 'LOCKED';
    end if;

    select max(position)
    into v_max_pos
    from public.evaluations e
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id;

    v_old_pos := v_row.position;
    v_new_pos := least(greatest(coalesce(p_harder_count, 0), 0), coalesce(v_max_pos, 0)::integer) + 1;

    -- Сжать личную шкалу (убрать старую позицию редактируемой оценки).
    if v_old_pos is not null then
        update public.evaluations
        set position = position - 1
        where user_id = v_uid
          and achievement_id <> p_achievement_id
          and position > v_old_pos;
    end if;

    -- Освободить место под новую позицию.
    update public.evaluations
    set position = position + 1
    where user_id = v_uid
      and achievement_id <> p_achievement_id
      and position >= v_new_pos;

    insert into public.evaluations (achievement_id, user_id, position)
    values (p_achievement_id, v_uid, v_new_pos)
    on conflict (achievement_id, user_id)
    do update set position = excluded.position;

    select count(*), avg(position)
    into v_count, v_avg
    from public.evaluations e
    where e.achievement_id = p_achievement_id;

    update public.achievements
    set average_position = v_avg,
        evaluation_count = v_count
    where id = p_achievement_id;

    -- Переход в ранжированные: присваиваем временный уникальный rank
    -- (сразу выполняется constraint achievements_rank_requires_ranked),
    -- фиксируем ти-брейкер момента и блокируем оценки.
    if v_count >= 3
       and (select ranking_status from public.achievements where id = p_achievement_id) = 'unknown'
    then
        update public.achievements
        set ranking_status = 'ranked',
            rank_order = random(),
            rank = coalesce(
                (select max(rank) from public.achievements where rank is not null),
                0
            ) + 1
        where id = p_achievement_id;

        update public.evaluations
        set locked = true
        where achievement_id = p_achievement_id;
    end if;

    perform public.recompute_ranks();

    return query
        select a.id,
               a.evaluation_count,
               a.average_position,
               a.ranking_status,
               e.position as my_position
        from public.achievements a
        left join public.evaluations e
               on e.achievement_id = a.id and e.user_id = v_uid
        where a.id = p_achievement_id;
end;
$$;

grant execute on function public.submit_evaluation(bigint, integer) to authenticated;
grant execute on function public.recompute_ranks() to authenticated;