-- GAL — целостность ранкинга: реальные (а не no-op) ограничения на achievements
-- 010_ranking_integrity.sql
--
-- Баг (bug-hunt 2026-09-28, critical): миграция 008 сделала
--     revoke update (rank, ranking_status, average_position, evaluation_count)
--         on public.achievements from authenticated;
-- но column-level REVOKE не действует, если у роли есть table-level грант, а
-- 002_rls.sql:297 выдаёт `grant select, insert, update on public.achievements
-- to authenticated`. Доказ. PostgreSQL 18 (REVOKE, Description): «if a role has
-- been granted privileges on a table, then revoking the same privileges from
-- individual columns will have no effect».
-- Итог: любой пользователь подделывал ranking-поля своего достижения через
-- PostgREST PATCH (RLS achievements_update_own_pending это допускает), а
-- `rank = 0` приводил к делению на ноль в player_leaderboard() (1000.0/rank).
--
-- 1. Ранг и метрики ранжирования изменяются только security definer-функциями.
--    Ранжирование-поля закрываются на уровне таблицы, безопасные колонки
--    выдаются явно (в т.ч. INSERT, иначе ранг можно было задать при создании).
-- 2. recompute_ranks() больше не callable любым пользователем: внешний вход
--    требует модератора, а submit_evaluation вызывает внутреннюю
--    recompute_ranks_internal() без гварда (иначе оценка обычного пользователя
--    ломалась бы). Внутренняя функция недоступна извне (EXECUTE снят у public).
-- 3. Страховка на уровне триггера: ranking-поля нельзя изменить не-дефайнером
--    даже при повторном table-level гранте.

-- +---------------------------------------------+
-- | 1. Настоящие гранты вместо column-level      |
-- +---------------------------------------------+

revoke insert, update on public.achievements from authenticated;

grant insert (
    title,
    description,
    requirements,
    category_id,
    creator_id,
    status
) on public.achievements to authenticated;

grant update (
    title,
    description,
    requirements,
    category_id,
    status,
    updated_at
) on public.achievements to authenticated;

-- +---------------------------------------------+
-- | 2. Гвард на recompute_ranks                  |
-- +---------------------------------------------+

-- Пересчёт рангов без проверки прав. Вызывается только из других
-- security definer-функций; EXECUTE у public/anonymous снят.
create or replace function public.recompute_ranks_internal()
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

revoke execute on function public.recompute_ranks_internal() from public;

-- Публичный вход: только модератор (единственный легитимный внешний
-- вызывающий — принятие жалобы, reports.decide_report). Тело на PL/pgSQL,
-- потому что нужны IF/RAISE/PERFORM.
create or replace function public.recompute_ranks()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    if not public.is_moderator() then
        raise exception 'NOT_MODERATOR'
            using errcode = '42501';
    end if;

    perform public.recompute_ranks_internal();
end;
$$;

grant execute on function public.recompute_ranks() to authenticated;

-- +---------------------------------------------+
-- | 3. Страховка триггером                       |
-- +---------------------------------------------+

-- Даже если table-level грант вернётся (alter default privileges, ручной grant),
-- ranking-поля нельзя задать/изменить из-под обычной роли: security definer-
-- функции выполняют UPDATE от имени владельца (current_user = 'postgres'),
-- а при direct REST/SQL current_user = 'authenticated'.
-- Функция намеренно БЕЗ security definer: иначе current_user внутри неё всегда
-- был бы владельцем и проверка стала бы безусловной.
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
    else
        new.ranking_status := old.ranking_status;
        new.rank := old.rank;
        new.rank_order := old.rank_order;
        new.average_position := old.average_position;
        new.evaluation_count := old.evaluation_count;
    end if;

    return new;
end;
$$;

create or replace trigger achievements_guard_ranking_fields
    before insert or update on public.achievements
    for each row execute procedure public.guard_ranking_fields();

revoke execute on function public.guard_ranking_fields() from public;

-- Синхронизация безопасных колонок с реальными грантами: приложение пишет
-- только title/description/requirements/category_id/creator_id/status, поэтому
-- автоответы (identity, created_at) грантить не нужно.
