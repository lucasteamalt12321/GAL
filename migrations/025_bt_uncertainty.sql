-- GAL — 025: надёжность Bradley–Terry (SE и 95% доверительный интервал)
-- 025_bt_uncertainty.sql
--
-- Python-сервис app/services/bradley_terry.py оценивает погрешность силы
-- кластерным бутстрэпом по личным шкалам. Здесь добавляются поля хранения:
--   bt_rating_se     — стандартная ошибка в единицах рейтинга;
--   bt_rating_ci_low, bt_rating_ci_high — границы 95% ДИ.
-- Пустые значения означают «данных недостаточно» (мало шкал/сравнений),
-- а не нулевую погрешность.
--
-- guard_ranking_fields и apply_bt_ratings переобъявляются целиком: последняя
-- версия функции должна знать о новых полях, иначе триггер откатит их, а RPC
-- не запишет. Порядок деплоя: миграция → приложение, потому что новый
-- RANKED_SELECT читает новые колонки (PostgREST вернёт ошибку на неизвестное
-- поле). Старый payload без ключей se/ci_low/ci_high запишет NULL.

alter table public.achievements
    add column if not exists bt_rating_se double precision,
    add column if not exists bt_rating_ci_low double precision,
    add column if not exists bt_rating_ci_high double precision;

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
        new.bt_rating_se := null;
        new.bt_rating_ci_low := null;
        new.bt_rating_ci_high := null;
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
        new.bt_rating_se := old.bt_rating_se;
        new.bt_rating_ci_low := old.bt_rating_ci_low;
        new.bt_rating_ci_high := old.bt_rating_ci_high;
        new.bt_updated_at := old.bt_updated_at;
    end if;

    return new;
end;
$$;

revoke execute on function public.guard_ranking_fields() from public;

-- +--------------------------------------------------+
-- | 2. Применение BT-рейтингов и интервалов           |
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
        bt_rating_se = r.se,
        bt_rating_ci_low = r.ci_low,
        bt_rating_ci_high = r.ci_high,
        bt_updated_at = now()
    from jsonb_to_recordset(p_ratings) as r(
        id bigint,
        strength double precision,
        rating double precision,
        comparisons integer,
        se double precision,
        ci_low double precision,
        ci_high double precision
    )
    where a.id = r.id;

    get diagnostics v_updated = row_count;

    perform public.recompute_ranks_internal();
    return v_updated;
end;
$$;

revoke execute on function public.apply_bt_ratings(jsonb) from public, anon, authenticated;
grant execute on function public.apply_bt_ratings(jsonb) to service_role;
