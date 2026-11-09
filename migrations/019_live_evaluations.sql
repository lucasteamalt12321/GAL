-- GAL — 019: новые оценки принимаются и после выхода в рейтинг
-- 019_live_evaluations.sql
--
-- Правило: каждый, кто выполнил достижение и отправляет заявку, обязан указать
-- сложность; после approve его оценка добавляется, и топ пересчитывается.
-- Оценки больше не «замораживаются» после перехода в ranked, поэтому новые
-- выполнения могут менять рейтинг.

update public.evaluations set locked = false where locked = true;

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
    v_max_pos double precision;
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

    select max(e.position)
    into v_max_pos
    from public.evaluations e
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id;

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

revoke execute on function public.submit_evaluation(bigint, integer) from public;
grant execute on function public.submit_evaluation(bigint, integer) to authenticated;

create or replace function public.apply_completion_evaluation(p_completion_id bigint)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid;
    v_aid bigint;
    v_status public.completion_status;
    v_harder integer;
    v_existing integer;
    v_ach_status public.achievement_status;
    v_rstatus public.ranking_status;
    v_max_pos double precision;
    v_new_pos integer;
    v_count integer;
    v_avg double precision;
begin
    if not (public.is_moderator() or coalesce(auth.role(), '') = 'service_role') then
        raise exception 'NOT_ALLOWED';
    end if;

    select c.user_id, c.achievement_id, c.status, c.requested_harder_count
    into v_uid, v_aid, v_status, v_harder
    from public.achievement_completions c
    where c.id = p_completion_id;

    if not found or v_status <> 'approved' then
        return false;
    end if;

    select a.status, a.ranking_status
    into v_ach_status, v_rstatus
    from public.achievements a
    where a.id = v_aid;

    if v_ach_status is distinct from 'published' then
        return false;
    end if;

    select count(*) into v_existing
    from public.evaluations e
    where e.user_id = v_uid;

    if v_harder is null then
        if v_existing = 0 then
            v_harder := 0;
        else
            return false;
        end if;
    end if;

    if exists (
        select 1 from public.evaluations e
        where e.achievement_id = v_aid and e.user_id = v_uid
    ) then
        return false;
    end if;

    perform pg_advisory_xact_lock(hashtext('gal_eval_' || v_uid::text));

    select max(e.position)
    into v_max_pos
    from public.evaluations e
    where e.user_id = v_uid
      and e.achievement_id <> v_aid;

    v_new_pos := least(
        greatest(v_harder, 0),
        coalesce(v_max_pos, 0)::integer
    ) + 1;

    update public.evaluations e
    set position = e.position + 1
    where e.user_id = v_uid
      and e.achievement_id <> v_aid
      and e.position >= v_new_pos;

    insert into public.evaluations (achievement_id, user_id, position, locked)
    values (v_aid, v_uid, v_new_pos, false);

    select count(*), avg(e.position)
    into v_count, v_avg
    from public.evaluations e
    where e.achievement_id = v_aid;

    update public.achievements
    set average_position = v_avg,
        evaluation_count = v_count
    where id = v_aid;

    if v_count >= 2 and v_rstatus <> 'ranked' then
        update public.achievements
        set ranking_status = 'ranked',
            rank_order = coalesce(rank_order, random()),
            rank = coalesce(
                (select max(a.rank) from public.achievements a where a.rank is not null),
                0
            ) + 1
        where id = v_aid;
    end if;

    perform public.recompute_ranks_internal();
    return true;
end;
$$;

revoke execute on function public.apply_completion_evaluation(bigint) from public;
grant execute on function public.apply_completion_evaluation(bigint) to authenticated;
