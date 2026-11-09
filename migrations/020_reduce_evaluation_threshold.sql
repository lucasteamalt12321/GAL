--- Миграция 020_reduce_evaluation_threshold.sql
--- Временно снижаем порог перехода в 'ranked' с 3 до 2 оценок.
--- Это нужно для ускорения рантинга новых достижений.

-- Удаляем старые функции и создаем новые с порогом 2

-- Обновляем submit_evaluation в 019_live_evaluations.sql
-- (используем существующую, но с измененным порогом через RPC)

-- Создаем новую функцию с порогом 2
create or replace function public.recompute_ranks_internal()
returns void
language sql
as $$
    with ranked as (
        select id,
               row_number() over (
                   order by average_position asc, rank_order asc, id asc
               ) as new_rank
        from public.achievements
        where ranking_status = 'ranked'
          and status = 'published'
          and average_position is not null
          and rank is not null
    )
    update public.achievements a
    set rank = r.new_rank
    from ranked r
    where a.id = r.id;
$$;

-- Обновляем функцию submit_evaluation для порога 2
create or replace function public.submit_evaluation(p_achievement_id bigint, p_harder_count integer)
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
#variable_conflict use_column
declare
    v_uid uuid := auth.uid();
    v_approved boolean;
    v_row evaluations%rowtype;
    v_old_pos double precision;
    v_max_pos double precision;
    v_new_pos double precision;
    v_count integer;
    v_avg double precision;
    v_rstatus public.ranking_status;
begin
    if v_uid is null then
        raise exception 'AUTH_REQUIRED';
    end if;

    -- Сериализация оценок одного пользователя
    perform pg_advisory_xact_lock(hashtext('gal_eval_' || v_uid::text));

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

    select max(e.position)
    into v_max_pos
    from public.evaluations e
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id
      and e.locked = false;

    v_old_pos := v_row.position;
    v_new_pos := least(
        greatest(coalesce(p_harder_count, 0), 0),
        coalesce(v_max_pos, 0)::integer
    ) + 1;

    if v_old_pos is not null then
        update public.evaluations e
        set position = e.position - 1
        where e.user_id = v_uid
          and e.achievement_id <> p_achievement_id
          and e.locked = false
          and e.position > v_old_pos;
    end if;

    update public.evaluations e
    set position = e.position + 1
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id
      and e.locked = false
      and e.position >= v_new_pos;

    insert into public.evaluations (achievement_id, user_id, position)
    values (p_achievement_id, v_uid, v_new_pos)
    on conflict (achievement_id, user_id)
    do update set position = excluded.position;

    select count(*), avg(e.position)
    into v_count, v_avg
    from public.evaluations e
    where e.achievement_id = p_achievement_id;

    update public.achievements
    set average_position = v_avg,
        evaluation_count = v_count
    where id = p_achievement_id;

    -- Переход в ranked при 2+ оценках
    if v_count >= 2 then
        select a.ranking_status
        into v_rstatus
        from public.achievements a
        where a.id = p_achievement_id;

        if v_rstatus = 'unknown' then
            update public.achievements
            set ranking_status = 'ranked',
                rank_order = random(),
                rank = coalesce(
                    (select max(a.rank) from public.achievements a where a.rank is not null),
                    0
                ) + 1,
                average_position = v_avg
            where id = p_achievement_id;

            perform public.recompute_ranks_internal();
        end if;
    end if;

    return query
        select p_achievement_id,
               v_count,
               v_avg,
               (select ranking_status from public.achievements where id = p_achievement_id),
               v_new_pos;
end;
$$;

revoke execute on function public.submit_evaluation(bigint, integer) from public, anon, authenticated;
grant execute on function public.submit_evaluation(bigint, integer) to authenticated;
