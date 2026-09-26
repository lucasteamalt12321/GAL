-- GAL — hardening: безопасность и целостность ядра ранжирования
-- 008_hardening.sql
-- 1. Exec на security definer RPC, которые пишут данные, — только authenticated
--    (по умолчанию Postgres выдаёт EXECUTE роли public, в т.ч. anon).
-- 2. recompute_ranks становится двухфазным: уникальность рангов сохраняется на
--    каждой границе стейтмента даже при немгновенном (immediate) частичном
--    индексе achievements_rank_unique -> исключает редкий 23505 при перестановках.
-- 3. Запрет подделки ранга/оценок через REST: UPDATE rank/ranking_status/
--    average_position/evaluation_count на achievements и прямое INSERT/UPDATE
--    на evaluations из-под authenticated (только submit_evaluation RPC).
-- 4. submit_evaluation: advisory lock на пользователя (сериализация оценок),
--    замороженные (locked) оценки исключены из сдвигов личной шкалы.
-- 5. Триггерные функции (handle_new_user, prevent_profile_role_change,
--    set_updated_at) — EXECUTE только у владельца (триггер не требует EXECUTE).
-- 6. Дубликаты pending-жалоб одного автора на одно достижение запрещены.

-- +---------------------------+
-- | 1. Ревоки/гранты EXECUTE  |
-- +---------------------------+

revoke execute on function public.submit_evaluation(bigint, integer) from public;
grant execute on function public.submit_evaluation(bigint, integer) to authenticated;

revoke execute on function public.recompute_ranks() from public;
grant execute on function public.recompute_ranks() to authenticated;

revoke execute on function public.player_leaderboard() from public;
grant execute on function public.player_leaderboard() to anon, authenticated;

revoke execute on function public.handle_new_user() from public;
revoke execute on function public.prevent_profile_role_change() from public;
revoke execute on function public.set_updated_at() from public;

-- +---------------------------+
-- | 2. Двухфазный recompute   |
-- +---------------------------+

create or replace function public.recompute_ranks()
returns void
language sql
security definer
set search_path = public
as $$
    -- Фаза 1: вывести все существующие ранги на безопасную высоту,
    -- порядок и уникальность сохраняются, а целевой диапазон 1..N опустошается.
    -- Уже поднятые ранее (>= 1000000000) не трогаем -> нерастущее хвостовое значение.
    update public.achievements
    set rank = rank + 1000000000
    where rank is not null and rank < 1000000000;

    -- Фаза 2: плотное перенумерование опубликованных ранжированных с 1..N.
    -- Новые значения строго меньше любого существующего (>= 1000000000),
    -- поэтому коллизия на achievements_rank_unique невозможна.
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

grant execute on function public.recompute_ranks() to authenticated;

-- +---------------------------+
-- | 3. Запрет прямой правки   |
-- +---------------------------+

-- Рангом и метриками управляет только RPC submit_evaluation / recompute_ranks
-- (security definer от имени владельца обходит grants и RLS).
revoke update (rank, ranking_status, average_position, evaluation_count)
    on public.achievements from authenticated;

-- Оценки создаются/меняются только через submit_evaluation.
-- RLS-политики evaluations_insert_own / evaluations_update_own_unlocked остаются
-- как defense-in-depth на случай повторного grant.
revoke insert, update on public.evaluations from authenticated;

-- +---------------------------+
-- | 4. submit_evaluation      |
-- +---------------------------+

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

    -- Сериализация оценок одного пользователя: исключает гонки позиций
    -- в личной шкале между параллельными submit_evaluation.
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

    -- Максимум только среди незамороженных: locked-оценки зафиксированы
    -- и не участвуют в построении активной личной шкалы.
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

    -- Сжать личную шкалу (убрать старую позицию редактируемой оценки).
    if v_old_pos is not null then
        update public.evaluations e
        set position = e.position - 1
        where e.user_id = v_uid
          and e.achievement_id <> p_achievement_id
          and e.locked = false
          and e.position > v_old_pos;
    end if;

    -- Освободить место под новую позицию.
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

    -- Переход в ранжированные: присваиваем временный уникальный rank
    -- (сразу выполняется constraint achievements_rank_requires_ranked),
    -- фиксируем ти-брейкер момента и блокируем оценки.
    if v_count >= 3 then
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
                ) + 1
            where id = p_achievement_id;

            update public.evaluations e
            set locked = true
            where e.achievement_id = p_achievement_id;
        end if;
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

-- +---------------------------+
-- | 6. Дубликаты pending-жалоб|
-- +---------------------------+

-- Приложение проверяет это на своём уровне (reports.create_report); индекс —
-- защита от гонки одновременных подач. После рассмотрения новая pending-жалоба
-- того же автора возможна снова (partial index по status = 'pending').
create unique index if not exists reports_pending_unique
    on public.reports (achievement_id, reporter_id)
    where status = 'pending';