-- GAL — 014: глобальный ранкинг по модели Bradley–Terry
-- 014_bradley_terry.sql
--
-- Идея (docs/idea.md): из личных шкал пользователей строятся попарные
-- сравнения, силы достижений оцениваются моделью Bradley–Terry. Python-сервис
-- app/services/bradley_terry.py считает силы, RPC apply_bt_ratings() атомарно
-- пишет их и пересчитывает ранги.
--
-- 1. Колонки bt_strength (логит-сила, больше = сложнее), bt_rating
--    (Elo-подобная шкала 1000 + 400*strength/ln10), bt_comparisons, bt_updated_at.
-- 2. guard_ranking_fields защищает новые поля от прямого REST-обновления.
-- 3. recompute_ranks_internal сортирует по bt_strength desc, затем по
--    average_position/rank_order/id как fallback и tie-break.
-- 4. apply_bt_ratings(jsonb) — security definer, доступна только service_role.

alter table public.achievements
    add column if not exists bt_strength double precision,
    add column if not exists bt_rating double precision,
    add column if not exists bt_comparisons integer not null default 0,
    add column if not exists bt_updated_at timestamptz;

create index if not exists achievements_bt_strength_idx
    on public.achievements (bt_strength desc nulls last)
    where status = 'published' and ranking_status = 'ranked';

-- +--------------------------------------------------+
-- | 1. Guard: bt-поля меняет только security definer  |
-- +--------------------------------------------------+

create or replace function public.guard_ranking_fields()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if current_user = 'postgres' then
        return new;
    end if;

    if tg_op = 'INSERT' then
        new.ranking_status := 'unknown';
        new.rank := null;
        new.rank_order := null;
        new.average_position := null;
        new.evaluation_count := 0;
        new.bt_strength := null;
        new.bt_rating := null;
        new.bt_comparisons := 0;
        new.bt_updated_at := null;
    else
        new.ranking_status := old.ranking_status;
        new.rank := old.rank;
        new.rank_order := old.rank_order;
        new.average_position := old.average_position;
        new.evaluation_count := old.evaluation_count;
        new.bt_strength := old.bt_strength;
        new.bt_rating := old.bt_rating;
        new.bt_comparisons := old.bt_comparisons;
        new.bt_updated_at := old.bt_updated_at;
    end if;

    return new;
end;
$$;

revoke execute on function public.guard_ranking_fields() from public;

-- +--------------------------------------------------+
-- | 2. Пересчёт рангов по BT-силе                     |
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

revoke execute on function public.recompute_ranks_internal() from public;

-- +--------------------------------------------------+
-- | 3. Применение BT-рейтингов                        |
-- +--------------------------------------------------+

create or replace function public.apply_bt_ratings(p_ratings jsonb)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_updated integer;
begin
    if p_ratings is null or jsonb_typeof(p_ratings) <> 'array' then
        raise exception 'INVALID_RATINGS';
    end if;

    -- Сериализуем параллельные пересчёты: последний записавший видит все
    -- оценки, но ранги не должны чередоваться между двумя апдейтами.
    perform pg_advisory_xact_lock(hashtext('gal_bt_apply'));

    update public.achievements a
    set bt_strength = r.strength,
        bt_rating = r.rating,
        bt_comparisons = coalesce(r.comparisons, 0),
        bt_updated_at = now()
    from jsonb_to_recordset(p_ratings) as r(
        id bigint,
        strength double precision,
        rating double precision,
        comparisons integer
    )
    where a.id = r.id;

    get diagnostics v_updated = row_count;

    perform public.recompute_ranks_internal();
    return v_updated;
end;
$$;

revoke execute on function public.apply_bt_ratings(jsonb) from public, anon, authenticated;
grant execute on function public.apply_bt_ratings(jsonb) to service_role;
