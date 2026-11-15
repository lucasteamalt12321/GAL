-- GAL — 030: идемпотентное применение временных рейтингов топ-5
-- 030_top5_apply_upsert.sql
--
-- В 027 apply_temporary_ratings делал `delete from achievement_temporary_ratings`
-- (очистка ВСЕГО кэша) и затем вставлял строки из payload. Advisory lock
-- сериализует вызовы, но не упорядочивает их по свежести payload: если stale
-- payload (рассчитанный до одобрения новой заявки) применяется после свежего,
-- очистка всего кэша стирает свежие строки для заявок, которых нет в stale
-- payload. Баг самозалечивается на следующем пересчёте, но окно гонки есть.
--
-- Здесь:
--   1. Удаляем только строки, потерявшие право на временный рейтинг (заявка
--      больше не approved, достижение не published, набрало 3 оценки или
--      вошло в постоянный топ-5). Условия повторяют eligibility из
--      bt_recompute._temporary_payload.
--   2. Вставляем payload через ON CONFLICT (achievement_id) DO UPDATE.
--
-- В стационарном режиме payload содержит все одобренные eligible-заявки,
-- поэтому eligible-строк вне payload нет и поведение совпадает с 027. Разница
-- проявляется только в гонке: свежие строки для eligible-заявок, отсутствующих
-- в stale payload, сохраняются, а не стираются.

create or replace function public.apply_temporary_ratings(p_ratings jsonb)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_inserted integer;
begin
    if coalesce(auth.role(), '') <> 'service_role' then
        raise exception 'NOT_ALLOWED';
    end if;
    if p_ratings is null or jsonb_typeof(p_ratings) <> 'array' then
        raise exception 'INVALID_RATINGS';
    end if;

    perform pg_advisory_xact_lock(hashtext('gal_top5_apply'));

    -- Удаляем только строки, потерявшие eligibility. Свежие строки одобренных
    -- заявок, отсутствующие в (возможно stale) payload, не стираются.
    delete from public.achievement_temporary_ratings tr
    where not exists (
        select 1
        from public.top5_applications app
        join public.achievements a
          on a.id = app.achievement_id
        where app.id = tr.application_id
          and app.achievement_id = tr.achievement_id
          and app.status = 'approved'
          and a.status = 'published'
          and a.evaluation_count < 3
          and (a.rank is null or a.rank > 5)
    );

    insert into public.achievement_temporary_ratings (
        achievement_id, application_id, strength, rating,
        evaluator_count, ordinary_evaluation_count, computed_at
    )
    select r.achievement_id,
           r.application_id,
           r.strength,
           r.rating,
           coalesce(r.evaluator_count, 0),
           coalesce(r.ordinary_evaluation_count, 0),
           now()
    from jsonb_to_recordset(p_ratings) as r(
        achievement_id bigint,
        application_id bigint,
        strength double precision,
        rating double precision,
        evaluator_count integer,
        ordinary_evaluation_count integer
    )
    join public.top5_applications app
      on app.id = r.application_id
     and app.status = 'approved'
     and app.achievement_id = r.achievement_id
    join public.achievements a
      on a.id = r.achievement_id
     and a.status = 'published'
     and a.evaluation_count < 3
    where r.strength is not null
      and r.rating is not null
    on conflict (achievement_id) do update set
        application_id = excluded.application_id,
        strength = excluded.strength,
        rating = excluded.rating,
        evaluator_count = excluded.evaluator_count,
        ordinary_evaluation_count = excluded.ordinary_evaluation_count,
        computed_at = excluded.computed_at;

    get diagnostics v_inserted = row_count;
    return v_inserted;
end;
$$;

revoke execute on function public.apply_temporary_ratings(jsonb)
    from public, anon, authenticated;
grant execute on function public.apply_temporary_ratings(jsonb)
    to service_role;