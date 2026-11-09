-- GAL — 018: автопервая оценка (якорь) и backfill
-- 018_auto_first_evaluation.sql
--
-- Первое подтверждённое выполнение пользователя автоматически становится
-- первой точкой его личной шкалы: если requested_harder_count не задан, а
-- других оценок нет, применяется позиция 1. Так пользователю не нужно вручную
-- «сохранять» первую оценку, и следующие достижения уже есть с чем сравнивать.

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

    if v_ach_status is distinct from 'published' or v_rstatus = 'ranked' then
        return false;
    end if;

    -- Сколько оценок уже есть у пользователя. Если это первая заявка без
    -- сохранённой сложности — делаем её якорем (позиция 1).
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

    insert into public.evaluations (achievement_id, user_id, position)
    values (v_aid, v_uid, v_new_pos);

    select count(*), avg(e.position)
    into v_count, v_avg
    from public.evaluations e
    where e.achievement_id = v_aid;

    update public.achievements
    set average_position = v_avg,
        evaluation_count = v_count
    where id = v_aid;

    if v_count >= 2 then
        update public.achievements
        set ranking_status = 'ranked',
            rank_order = random(),
            rank = coalesce(
                (select max(a.rank) from public.achievements a where a.rank is not null),
                0
            ) + 1
        where id = v_aid;

        update public.evaluations e
        set locked = true
        where e.achievement_id = v_aid;
    end if;

    perform public.recompute_ranks_internal();
    return true;
end;
$$;

revoke execute on function public.apply_completion_evaluation(bigint) from public;
grant execute on function public.apply_completion_evaluation(bigint) to authenticated;

-- Backfill: у кого есть approved published completion, но нет ни одной оценки —
-- первая по времени заявка становится якорем.
do $$
declare
    r record;
begin
    perform set_config('request.jwt.claims', '{"role":"service_role"}', true);
    for r in
        select c.id
        from public.achievement_completions c
        join public.achievements a on a.id = c.achievement_id
        where c.status = 'approved'
          and a.status = 'published'
          and not exists (
              select 1 from public.evaluations e where e.user_id = c.user_id
          )
        order by c.approved_at asc nulls last, c.id asc
    loop
        perform public.apply_completion_evaluation(r.id);
    end loop;
end;
$$;
