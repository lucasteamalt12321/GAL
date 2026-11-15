-- GAL — 029: кламп позиции оценки по числу других оценок
-- 029_submit_evaluation_position_clamp.sql
--
-- Баг: submit_evaluation ограничивал вставляемую позицию величиной v_max_pos —
-- максимумом position ДРУГИХ оценок ДО сжатия шкалы. При редактировании не
-- последней оценки остальные сжимаются до плотных 1..N-1, но кламп разрешал
-- p_harder_count = N (старый максимум), давая v_new_pos = N+1 и дыру на позиции
-- N. Дыра искажала average_position и сохранялась до следующего пересчёта.
-- Через роутер недостижимо (там clamp по len(others)), но прямой вызов RPC
-- принимал такое значение.
--
-- Фикс: кламп по числу других оценок (count). После сжатия другие оценки
-- плотные 1..count, поэтому допустимая позиция вставки — 1..count+1. Это
-- корректно и для новой оценки, и для редактирования. Остальные инварианты
-- 022 (порог 2, NOT_FOUND, advisory lock, security definer, двухфазный bump
-- через recompute_ranks_internal) сохранены без изменений.

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
#variable_conflict use_column
declare
    v_uid uuid := auth.uid();
    v_approved boolean;
    v_row evaluations%rowtype;
    v_old_pos double precision;
    v_other_count integer;
    v_new_pos double precision;
    v_count integer;
    v_avg double precision;
    v_rstatus public.ranking_status;
begin
    if v_uid is null then
        raise exception 'AUTH_REQUIRED';
    end if;

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

    select a.ranking_status
    into v_rstatus
    from public.achievements a
    where a.id = p_achievement_id;

    if v_rstatus is null then
        raise exception 'NOT_FOUND';
    end if;

    select *
    into v_row
    from public.evaluations e
    where e.achievement_id = p_achievement_id
      and e.user_id = v_uid;

    -- Число других оценок инвариантно при сжатии и задаёт плотный максимум
    -- шкалы после удаления редактируемой позиции.
    select count(*)
    into v_other_count
    from public.evaluations e
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id;

    v_old_pos := v_row.position;
    v_new_pos := least(
        greatest(coalesce(p_harder_count, 0), 0),
        v_other_count
    ) + 1;

    if v_old_pos is not null then
        update public.evaluations e
        set position = e.position - 1
        where e.user_id = v_uid
          and e.achievement_id <> p_achievement_id
          and e.position > v_old_pos;
    end if;

    update public.evaluations e
    set position = e.position + 1
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id
      and e.position >= v_new_pos;

    insert into public.evaluations (achievement_id, user_id, position, locked)
    values (p_achievement_id, v_uid, v_new_pos, false)
    on conflict (achievement_id, user_id)
    do update set position = excluded.position, locked = false;

    select count(*), avg(e.position)
    into v_count, v_avg
    from public.evaluations e
    where e.achievement_id = p_achievement_id;

    update public.achievements
    set average_position = v_avg,
        evaluation_count = v_count
    where id = p_achievement_id;

    if v_count >= 2 and v_rstatus <> 'ranked' then
        update public.achievements
        set ranking_status = 'ranked',
            rank_order = coalesce(rank_order, random()),
            rank = coalesce(
                (select max(a.rank) from public.achievements a where a.rank is not null),
                0
            ) + 1
        where id = p_achievement_id;
    end if;

    perform public.recompute_ranks_internal();

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

revoke execute on function public.submit_evaluation(bigint, integer)
    from public, anon;
grant execute on function public.submit_evaluation(bigint, integer)
    to authenticated, service_role;