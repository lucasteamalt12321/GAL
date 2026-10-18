-- GAL — целостность личной шкалы и lock-инварианта оценок
-- 011_evaluation_lock.sql
--
-- Баги (bug-hunt 2026-09-28, medium), оба в submit_evaluation из 008:
--
-- 1. Шкала строилась только по `locked = false`, а валидация позиции в
--    приложении (app/routers/evaluations.py) считает по ВСЕМ оценкам
--    пользователя (list_user_scale отдаёт и locked). Состояние «все оценки
--    locked» — норма (переход в ranked при 3+ оценках), поэтому v_max_pos
--    становился NULL -> v_new_pos = 1 -> в шкале появлялись две оценки на
--    позиции 1, а выбранная пользователем позиция молча клампилась.
--    Фикс: locked-оценки участвуют в max() и в обоих сдвигах -> позиции
--    всегда плотные 1..N, и SQL совпадает с тем, что проверяет роутер.
--
-- 2. `set locked = true` выполнялся только в ветке перехода unknown -> ranked.
--    Новый пользователь с approved completion мог добавить оценку на уже
--    ранжированное достижение, и она навсегда оставалась locked = false, т.е.
--    average_position и мировой rank переигрывались после ранжирования.
--    Фикс: ranked-достижение больше не принимает оценок (LOCKED).

-- +---------------------------------------------+
-- | 1. Заморозить «дырявые» незаблокированные   |
-- |    оценки на уже ранжированных достижениях   |
-- +---------------------------------------------+

update public.evaluations e
set locked = true
from public.achievements a
where a.id = e.achievement_id
  and a.ranking_status = 'ranked'
  and e.locked = false;

-- +---------------------------------------------+
-- | 2. submit_evaluation заново                  |
-- +---------------------------------------------+

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

    -- Ранжированное достижение закрыто навсегда: иначе average_position
    -- и мировой rank можно переигрывать после перехода в ranked.
    select a.ranking_status
    into v_rstatus
    from public.achievements a
    where a.id = p_achievement_id;

    if v_rstatus is null then
        raise exception 'NOT_FOUND';
    end if;

    if v_rstatus = 'ranked' then
        raise exception 'LOCKED';
    end if;

    select *
    into v_row
    from public.evaluations e
    where e.achievement_id = p_achievement_id
      and e.user_id = v_uid;

    if v_row.id is not null and v_row.locked then
        raise exception 'LOCKED';
    end if;

    -- Максимум по всей шкале, включая замороженные: позиции плотные 1..N.
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

    -- Сжать личную шкалу (убрать старую позицию редактируемой оценки).
    if v_old_pos is not null then
        update public.evaluations e
        set position = e.position - 1
        where e.user_id = v_uid
          and e.achievement_id <> p_achievement_id
          and e.position > v_old_pos;
    end if;

    -- Освободить место под новую позицию.
    update public.evaluations e
    set position = e.position + 1
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id
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

    -- Внутренний пересчёт без гварда is_moderator() (010): обычный пользователь
    -- не модератор, но пересчёт обязателен при каждой оценке.
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
