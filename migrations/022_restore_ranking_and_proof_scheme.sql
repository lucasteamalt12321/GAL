-- GAL — 022: восстановление канонического ранжирования и защита ссылок
-- 022_restore_ranking_and_proof_scheme.sql
--
-- Файл 020_reduce_evaluation_threshold.sql переопределял recompute_ranks_internal
-- без сортировки по bt_strength, без двухфазного подъёма rank и без
-- security definer. Он не применялся к проду, но оставался в репозитории и
-- применился бы на чистой БД. Здесь:
--   1. Возвращаем каноническую версию recompute_ranks_internal (как в 014).
--   2. Пересоздаём submit_evaluation и apply_completion_evaluation с порогом 2,
--      сохраняя все инварианты 019 (NOT_FOUND, advisory lock, ranked-переход).
--   3. Запрещаем сохранять в proofs ссылки с небезопасной схемой (javascript:,
--      data: и т.п.) на уровне БД. Ограничение добавлено NOT VALID: старые
--      строки не проверяются, новые — проверяются.

-- +--------------------------------------------------+
-- | 1. Канонический пересчёт рангов (BT, двухфазный)  |
-- +--------------------------------------------------+

create or replace function public.recompute_ranks_internal()
returns void
language sql
security definer
set search_path = public
as $$
    update public.achievements
    set rank = rank + 1000000000
    where rank is not null and rank < 1000000000;

    with ranked as (
        select id,
               row_number() over (
                   order by bt_strength desc nulls last,
                            average_position asc,
                            rank_order asc,
                            id asc
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

revoke execute on function public.recompute_ranks_internal()
    from public, anon, authenticated, service_role;

-- +--------------------------------------------------+
-- | 2. Оценка пользователя (порог 2, инварианты 019)  |
-- +--------------------------------------------------+

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

revoke execute on function public.submit_evaluation(bigint, integer)
    from public, anon;
grant execute on function public.submit_evaluation(bigint, integer)
    to authenticated, service_role;

-- +--------------------------------------------------+
-- | 3. Оценка после approve заявки (порог 2)          |
-- +--------------------------------------------------+

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

revoke execute on function public.apply_completion_evaluation(bigint)
    from public, anon;
grant execute on function public.apply_completion_evaluation(bigint)
    to authenticated, service_role;

-- +--------------------------------------------------+
-- | 4. Ссылка-доказательство: только http/https       |
-- +--------------------------------------------------+

alter table public.proofs
    drop constraint if exists proofs_link_scheme;
alter table public.proofs
    add constraint proofs_link_scheme
    check (
        proof_type <> 'link'
        or storage_path ~* '^https?://[^[:space:]]+$'
    ) not valid;
