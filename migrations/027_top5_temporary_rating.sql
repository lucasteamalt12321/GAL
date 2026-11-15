-- GAL — 027: предварительный рейтинг топ-5
-- 027_top5_temporary_rating.sql
--
-- Автор опубликованного достижения может подать заявку на попадание в топ-5:
-- позиция 1–5, доказательство выполнения и обоснование. Заявку рассматривает
-- модератор: одобрить (временное место со звёздочкой), отклонить с причиной
-- или отправить на независимую экспертизу приглашённым топ-игрокам.
--
-- Отдельные таблицы, а не общие evaluations/moderation_reviews:
--   * top5_applications      — заявка, решение, причина, конвертация;
--   * top5_evaluations       — оценки модераторов и экспертов (1–6);
--   * top5_expert_requests   — приглашения экспертов и их ответы;
--   * top5_events            — append-only журнал решений и голосов;
--   * achievement_temporary_ratings — кэш временной силы для общего топа.
-- Обычные оценки пользователей не смешиваются с экспертными и не завышают
-- evaluation_count: это отдельные строки с явным типом оценщика.
--
-- Переход: 0–2 обычных оценки — временный рейтинг (если заявка одобрена),
-- с третьей независимой оценкой заявка становится converted, временный кэш
-- удаляется, достижение показывается в постоянном рейтинге без звёздочки.
-- Постоянный алгоритм Bradley–Terry не меняется; временная сила считается
-- против текущего постоянного топа и пишется через service-role RPC.
--
-- Порядок применения: после 026. Функция user_completed_achievements здесь
-- пересоздаётся с полями is_temporary/permanent_rank; применение 026 поверх
-- 027 вернёт старую сигнатуру и потеряет эти поля.
--
-- Миграция самодостаточна относительно 025: колонки интервалов BT создаются
-- ниже, если 025 ещё не применена, а guard защищает их от прямых изменений.

-- +--------------------------------------------------+
-- | 0. Колонки интервалов BT (совместимость с 025)     |
-- +--------------------------------------------------+

alter table public.achievements
    add column if not exists bt_rating_se double precision,
    add column if not exists bt_rating_ci_low double precision,
    add column if not exists bt_rating_ci_high double precision;

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
-- | 1. Типы                                            |
-- +--------------------------------------------------+

do $$
begin
    create type public.top5_application_status as enum (
        'pending', 'expert_review', 'approved', 'rejected', 'converted', 'cancelled'
    );
exception
    when duplicate_object then null;
end;
$$;

do $$
begin
    create type public.top5_evaluator_type as enum ('moderator', 'expert');
exception
    when duplicate_object then null;
end;
$$;

do $$
begin
    create type public.top5_request_status as enum ('pending', 'submitted', 'declined');
exception
    when duplicate_object then null;
end;
$$;

-- +--------------------------------------------------+
-- | 2. Таблицы                                         |
-- +--------------------------------------------------+

create table if not exists public.top5_applications (
    id bigint generated always as identity primary key,
    achievement_id bigint not null references public.achievements (id) on delete cascade,
    applicant_id uuid not null references public.profiles (id) on delete cascade,
    proposed_position smallint not null,
    justification text not null default '',
    evidence_url text not null,
    evidence_note text not null default '',
    status public.top5_application_status not null default 'pending',
    decided_by uuid references public.profiles (id) on delete set null,
    decided_at timestamptz,
    decision_reason text not null default '',
    converted_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint top5_applications_position_range
        check (proposed_position between 1 and 5),
    constraint top5_applications_evidence_scheme
        check (evidence_url ~* '^https?://[^[:space:]]+$'),
    constraint top5_applications_evidence_length
        check (char_length(evidence_url) <= 2000),
    constraint top5_applications_justification_length
        check (char_length(justification) <= 4000),
    constraint top5_applications_reason_length
        check (char_length(decision_reason) <= 2000)
);

-- Одна активная заявка на достижение; после отказа/отзыва автор может подать
-- новую. converted — уже не активная: достижение перешло в постоянный топ.
create unique index if not exists top5_applications_active_unique
    on public.top5_applications (achievement_id)
    where status in ('pending', 'expert_review', 'approved');
create index if not exists top5_applications_status_idx
    on public.top5_applications (status, created_at);
create index if not exists top5_applications_applicant_idx
    on public.top5_applications (applicant_id, created_at desc);

create table if not exists public.top5_evaluations (
    id bigint generated always as identity primary key,
    application_id bigint not null references public.top5_applications (id) on delete cascade,
    evaluator_id uuid not null references public.profiles (id) on delete cascade,
    evaluator_type public.top5_evaluator_type not null,
    estimated_position smallint not null,
    note text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint top5_evaluations_position_range
        check (estimated_position between 1 and 6),
    constraint top5_evaluations_note_length
        check (char_length(note) <= 2000),
    constraint top5_evaluations_unique
        unique (application_id, evaluator_id)
);

create index if not exists top5_evaluations_application_idx
    on public.top5_evaluations (application_id);
create index if not exists top5_evaluations_evaluator_idx
    on public.top5_evaluations (evaluator_id);

create table if not exists public.top5_expert_requests (
    id bigint generated always as identity primary key,
    application_id bigint not null references public.top5_applications (id) on delete cascade,
    expert_id uuid not null references public.profiles (id) on delete cascade,
    invited_by uuid not null references public.profiles (id) on delete cascade,
    status public.top5_request_status not null default 'pending',
    note text not null default '',
    created_at timestamptz not null default now(),
    responded_at timestamptz,
    constraint top5_expert_requests_note_length
        check (char_length(note) <= 1000),
    constraint top5_expert_requests_unique
        unique (application_id, expert_id)
);

create index if not exists top5_expert_requests_expert_idx
    on public.top5_expert_requests (expert_id, status);
create index if not exists top5_expert_requests_application_idx
    on public.top5_expert_requests (application_id, status);

create table if not exists public.top5_events (
    id bigint generated always as identity primary key,
    application_id bigint not null references public.top5_applications (id) on delete cascade,
    actor_id uuid references public.profiles (id) on delete set null,
    event_type text not null,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists top5_events_application_idx
    on public.top5_events (application_id, created_at);

create table if not exists public.achievement_temporary_ratings (
    achievement_id bigint primary key
        references public.achievements (id) on delete cascade,
    application_id bigint not null
        references public.top5_applications (id) on delete cascade,
    strength double precision not null,
    rating double precision not null,
    evaluator_count integer not null default 0,
    ordinary_evaluation_count integer not null default 0,
    computed_at timestamptz not null default now(),
    constraint achievement_temporary_ratings_counts_non_negative
        check (evaluator_count >= 0 and ordinary_evaluation_count >= 0)
);

create index if not exists achievement_temporary_ratings_application_idx
    on public.achievement_temporary_ratings (application_id);

-- +--------------------------------------------------+
-- | 3. Служебные функции                               |
-- +--------------------------------------------------+

-- Внутри security definer current_user — владелец функции (postgres), а не
-- вызывающий, поэтому для RPC авторизацию проверяем отдельно по auth.role().
create or replace function public.top5_is_privileged()
returns boolean
language sql
stable
set search_path = public
as $$
    select current_user = 'postgres'
        or coalesce(auth.role(), '') = 'service_role'
$$;

create or replace function public.top5_can_view_application(p_application_id bigint)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.top5_applications a
        where a.id = p_application_id
          and (a.applicant_id = auth.uid() or public.is_moderator())
    )
    or exists (
        select 1
        from public.top5_expert_requests r
        where r.application_id = p_application_id
          and r.expert_id = auth.uid()
    )
$$;

create or replace function public.top5_application_guard()
returns trigger
language plpgsql
set search_path = public
as $$
declare
    v_actor uuid := auth.uid();
    v_privileged boolean := public.top5_is_privileged();
    v_creator uuid;
    v_ach_status public.achievement_status;
    v_rank integer;
    v_count integer;
begin
    if tg_op = 'INSERT' then
        select a.creator_id, a.status, a.rank, a.evaluation_count
        into v_creator, v_ach_status, v_rank, v_count
        from public.achievements a
        where a.id = new.achievement_id;

        if not found then
            raise exception 'ACHIEVEMENT_NOT_FOUND';
        end if;
        if v_ach_status <> 'published' then
            raise exception 'ACHIEVEMENT_NOT_PUBLISHED';
        end if;
        if v_count >= 3 then
            raise exception 'ALREADY_PERMANENT';
        end if;
        if v_rank is not null and v_rank <= 5 then
            raise exception 'ALREADY_IN_TOP5';
        end if;

        if not v_privileged then
            if v_actor is null or v_creator is distinct from v_actor then
                raise exception 'NOT_AUTHOR';
            end if;
            new.applicant_id := v_actor;
            new.status := 'pending';
            new.decided_by := null;
            new.decided_at := null;
            new.decision_reason := '';
            new.converted_at := null;
        end if;
        return new;
    end if;

    if new.achievement_id is distinct from old.achievement_id
       or new.applicant_id is distinct from old.applicant_id then
        raise exception 'TARGET_IMMUTABLE';
    end if;

    if new.proposed_position is distinct from old.proposed_position
       or new.justification is distinct from old.justification
       or new.evidence_url is distinct from old.evidence_url
       or new.evidence_note is distinct from old.evidence_note then
        if not v_privileged and (
            v_actor is distinct from old.applicant_id
            or old.status <> 'pending'
        ) then
            raise exception 'EDIT_NOT_ALLOWED';
        end if;
    end if;

    if old.status in ('converted', 'cancelled') then
        raise exception 'TERMINAL_APPLICATION';
    end if;
    if new.status = 'converted' and not v_privileged then
        raise exception 'SYSTEM_ONLY';
    end if;

    if v_privileged then
        if new.status = 'approved' and new.status is distinct from old.status then
            select a.evaluation_count, a.rank
            into v_count, v_rank
            from public.achievements a
            where a.id = new.achievement_id;
            if v_count >= 3 then
                raise exception 'ALREADY_PERMANENT';
            end if;
            if v_rank is not null and v_rank <= 5 then
                raise exception 'ALREADY_IN_TOP5';
            end if;
            new.converted_at := null;
        end if;
        return new;
    end if;

    -- Автор может только отозвать свою заявку и не может её одобрить.
    if v_actor = old.applicant_id and not public.is_moderator() then
        if new.status is distinct from old.status then
            if new.status <> 'cancelled'
               or old.status not in ('pending', 'expert_review') then
                raise exception 'AUTHOR_CANNOT_DECIDE';
            end if;
            new.decided_by := v_actor;
            new.decided_at := now();
            if btrim(new.decision_reason) = '' then
                new.decision_reason := 'Заявка отозвана автором';
            end if;
        else
            -- Правка полей pending-заявки не должна менять решение.
            new.decided_by := old.decided_by;
            new.decided_at := old.decided_at;
            new.decision_reason := old.decision_reason;
        end if;
        return new;
    end if;

    if not public.is_moderator() then
        raise exception 'NOT_ALLOWED';
    end if;

    if new.status is distinct from old.status then
        if old.applicant_id = v_actor and new.status <> 'cancelled' then
            raise exception 'SELF_DECISION';
        end if;
        if new.status = 'rejected' and btrim(new.decision_reason) = '' then
            raise exception 'REASON_REQUIRED';
        end if;
        if new.status = 'approved' then
            select a.evaluation_count, a.rank
            into v_count, v_rank
            from public.achievements a
            where a.id = new.achievement_id;
            if v_count >= 3 then
                raise exception 'ALREADY_PERMANENT';
            end if;
            if v_rank is not null and v_rank <= 5 then
                raise exception 'ALREADY_IN_TOP5';
            end if;
            new.converted_at := null;
        end if;
        new.decided_by := v_actor;
        new.decided_at := now();
    else
        -- Модератор может исправить причину решения, не меняя статус.
        new.decided_by := old.decided_by;
        new.decided_at := old.decided_at;
    end if;
    return new;
end;
$$;

create or replace function public.top5_expert_request_guard()
returns trigger
language plpgsql
set search_path = public
as $$
declare
    v_actor uuid := auth.uid();
    v_privileged boolean := public.top5_is_privileged();
    v_status public.top5_application_status;
    v_applicant uuid;
    v_creator uuid;
begin
    select app.status, app.applicant_id, a.creator_id
    into v_status, v_applicant, v_creator
    from public.top5_applications app
    join public.achievements a on a.id = app.achievement_id
    where app.id = new.application_id;

    if not found then
        raise exception 'APPLICATION_NOT_FOUND';
    end if;
    if new.expert_id in (v_applicant, v_creator) then
        raise exception 'NOT_INDEPENDENT';
    end if;

    if tg_op = 'INSERT' then
        if v_status not in ('pending', 'expert_review') then
            raise exception 'APPLICATION_CLOSED';
        end if;
        if not v_privileged then
            if not public.is_moderator() then
                raise exception 'NOT_ALLOWED';
            end if;
            new.invited_by := v_actor;
            new.status := 'pending';
            new.responded_at := null;
        end if;
        return new;
    end if;

    if new.application_id is distinct from old.application_id
       or new.expert_id is distinct from old.expert_id
       or new.invited_by is distinct from old.invited_by then
        raise exception 'REQUEST_IMMUTABLE';
    end if;

    if v_privileged then
        return new;
    end if;

    if v_actor = old.expert_id then
        if old.status not in ('pending', 'submitted') then
            raise exception 'INVALID_STATUS';
        end if;
        if new.status not in ('submitted', 'declined') then
            raise exception 'INVALID_STATUS';
        end if;
        if new.status = 'declined' and old.status = 'submitted' then
            raise exception 'INVALID_STATUS';
        end if;
        if new.status = 'declined' and btrim(new.note) = '' then
            raise exception 'REASON_REQUIRED';
        end if;
        new.responded_at := coalesce(new.responded_at, now());
        return new;
    end if;

    if public.is_moderator() then
        if new.status not in ('pending', 'submitted', 'declined') then
            raise exception 'INVALID_STATUS';
        end if;
        new.responded_at := case
            when new.status = 'pending' then null
            else coalesce(new.responded_at, now())
        end;
        return new;
    end if;

    raise exception 'NOT_ALLOWED';
end;
$$;

create or replace function public.top5_evaluation_guard()
returns trigger
language plpgsql
set search_path = public
as $$
declare
    v_actor uuid := auth.uid();
    v_privileged boolean := public.top5_is_privileged();
    v_status public.top5_application_status;
    v_applicant uuid;
    v_creator uuid;
begin
    select app.status, app.applicant_id, a.creator_id
    into v_status, v_applicant, v_creator
    from public.top5_applications app
    join public.achievements a on a.id = app.achievement_id
    where app.id = new.application_id;

    if not found then
        raise exception 'APPLICATION_NOT_FOUND';
    end if;
    if v_status in ('converted', 'cancelled', 'rejected') then
        raise exception 'APPLICATION_CLOSED';
    end if;

    if tg_op = 'INSERT' then
        if new.evaluator_id in (v_applicant, v_creator) then
            raise exception 'NOT_INDEPENDENT';
        end if;
        if not v_privileged then
            if new.evaluator_id is distinct from v_actor then
                raise exception 'NOT_SELF';
            end if;
            if new.evaluator_type = 'moderator' and not public.is_moderator() then
                raise exception 'NOT_MODERATOR';
            end if;
            if new.evaluator_type = 'expert' and not exists (
                select 1
                from public.top5_expert_requests r
                where r.application_id = new.application_id
                  and r.expert_id = v_actor
                  and r.status in ('pending', 'submitted')
            ) then
                raise exception 'NOT_INVITED';
            end if;
        end if;
    else
        if new.application_id is distinct from old.application_id
           or new.evaluator_id is distinct from old.evaluator_id
           or new.evaluator_type is distinct from old.evaluator_type then
            raise exception 'EVALUATION_IMMUTABLE';
        end if;
        if not v_privileged and new.evaluator_id is distinct from v_actor then
            raise exception 'NOT_SELF';
        end if;
    end if;

    if new.evaluator_type = 'expert' then
        update public.top5_expert_requests
        set status = 'submitted',
            responded_at = coalesce(responded_at, now())
        where application_id = new.application_id
          and expert_id = new.evaluator_id;
    end if;

    return new;
end;
$$;

-- +--------------------------------------------------+
-- | 4. Журнал и переход на третьей оценке              |
-- +--------------------------------------------------+

create or replace function public.top5_application_event()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if tg_op = 'INSERT' then
        insert into public.top5_events (application_id, actor_id, event_type, payload)
        values (
            new.id,
            auth.uid(),
            'submitted',
            jsonb_build_object(
                'status', new.status,
                'proposed_position', new.proposed_position
            )
        );
    elsif new.status is distinct from old.status then
        insert into public.top5_events (application_id, actor_id, event_type, payload)
        values (
            new.id,
            auth.uid(),
            'status_changed',
            jsonb_build_object(
                'from', old.status,
                'to', new.status,
                'reason', new.decision_reason
            )
        );
    elsif new.decision_reason is distinct from old.decision_reason then
        insert into public.top5_events (application_id, actor_id, event_type, payload)
        values (
            new.id,
            auth.uid(),
            'reason_changed',
            jsonb_build_object('reason', new.decision_reason)
        );
    end if;
    return new;
end;
$$;

create or replace function public.top5_expert_request_event()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if tg_op = 'INSERT' then
        insert into public.top5_events (application_id, actor_id, event_type, payload)
        values (
            new.application_id,
            auth.uid(),
            'expert_invited',
            jsonb_build_object('expert_id', new.expert_id)
        );
    elsif new.status is distinct from old.status then
        insert into public.top5_events (application_id, actor_id, event_type, payload)
        values (
            new.application_id,
            auth.uid(),
            'expert_status_changed',
            jsonb_build_object(
                'expert_id', new.expert_id,
                'from', old.status,
                'to', new.status
            )
        );
    end if;
    return new;
end;
$$;

create or replace function public.top5_evaluation_event()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.top5_events (application_id, actor_id, event_type, payload)
    values (
        new.application_id,
        auth.uid(),
        case when tg_op = 'INSERT' then 'evaluation_recorded' else 'evaluation_updated' end,
        jsonb_build_object(
            'evaluator_id', new.evaluator_id,
            'evaluator_type', new.evaluator_type,
            'estimated_position', new.estimated_position
        )
    );
    return new;
end;
$$;

create or replace function public.top5_convert_after_third_evaluation()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_count integer;
    v_app record;
    v_changed boolean := false;
begin
    select count(*)
    into v_count
    from public.evaluations e
    where e.achievement_id = new.achievement_id;

    if v_count < 3 then
        return new;
    end if;

    for v_app in
        select id
        from public.top5_applications
        where achievement_id = new.achievement_id
          and status in ('pending', 'expert_review', 'approved')
    loop
        update public.top5_applications
        set status = 'converted',
            converted_at = now(),
            decision_reason = case
                when btrim(decision_reason) = ''
                    then 'Автоматическая конвертация после третьей оценки'
                else decision_reason
            end
        where id = v_app.id;
        v_changed := true;
    end loop;

    if v_changed then
        delete from public.achievement_temporary_ratings
        where achievement_id = new.achievement_id;
        perform public.recompute_ranks_internal();
    end if;

    return new;
end;
$$;

create or replace function public.top5_clear_temporary_on_close()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if old.status = 'approved' and new.status is distinct from 'approved' then
        delete from public.achievement_temporary_ratings
        where application_id = new.id;
    end if;
    return new;
end;
$$;

-- +--------------------------------------------------+
-- | 5. Триггеры                                        |
-- +--------------------------------------------------+

drop trigger if exists top5_applications_set_updated_at on public.top5_applications;
create trigger top5_applications_set_updated_at
    before update on public.top5_applications
    for each row execute procedure public.set_updated_at();

drop trigger if exists top5_applications_guard on public.top5_applications;
create trigger top5_applications_guard
    before insert or update on public.top5_applications
    for each row execute procedure public.top5_application_guard();

drop trigger if exists top5_applications_event on public.top5_applications;
create trigger top5_applications_event
    after insert or update on public.top5_applications
    for each row execute procedure public.top5_application_event();

drop trigger if exists top5_applications_clear_temporary on public.top5_applications;
create trigger top5_applications_clear_temporary
    after update on public.top5_applications
    for each row execute procedure public.top5_clear_temporary_on_close();

drop trigger if exists top5_evaluations_set_updated_at on public.top5_evaluations;
create trigger top5_evaluations_set_updated_at
    before update on public.top5_evaluations
    for each row execute procedure public.set_updated_at();

drop trigger if exists top5_evaluations_guard on public.top5_evaluations;
create trigger top5_evaluations_guard
    before insert or update on public.top5_evaluations
    for each row execute procedure public.top5_evaluation_guard();

drop trigger if exists top5_evaluations_event on public.top5_evaluations;
create trigger top5_evaluations_event
    after insert or update on public.top5_evaluations
    for each row execute procedure public.top5_evaluation_event();

drop trigger if exists top5_expert_requests_guard on public.top5_expert_requests;
create trigger top5_expert_requests_guard
    before insert or update on public.top5_expert_requests
    for each row execute procedure public.top5_expert_request_guard();

drop trigger if exists top5_expert_requests_event on public.top5_expert_requests;
create trigger top5_expert_requests_event
    after insert or update on public.top5_expert_requests
    for each row execute procedure public.top5_expert_request_event();

drop trigger if exists evaluations_top5_convert on public.evaluations;
create trigger evaluations_top5_convert
    after insert on public.evaluations
    for each row execute procedure public.top5_convert_after_third_evaluation();

-- +--------------------------------------------------+
-- | 6. RLS                                             |
-- +--------------------------------------------------+

alter table public.top5_applications enable row level security;
alter table public.top5_evaluations enable row level security;
alter table public.top5_expert_requests enable row level security;
alter table public.top5_events enable row level security;
alter table public.achievement_temporary_ratings enable row level security;

drop policy if exists top5_applications_select on public.top5_applications;
create policy top5_applications_select on public.top5_applications
    for select
    using (public.top5_can_view_application(id));

drop policy if exists top5_applications_insert on public.top5_applications;
create policy top5_applications_insert on public.top5_applications
    for insert
    with check (
        applicant_id = auth.uid()
        and exists (
            select 1
            from public.achievements a
            where a.id = achievement_id
              and a.creator_id = auth.uid()
              and a.status = 'published'
        )
    );

drop policy if exists top5_applications_update on public.top5_applications;
create policy top5_applications_update on public.top5_applications
    for update
    using (applicant_id = auth.uid() or public.is_moderator())
    with check (applicant_id = auth.uid() or public.is_moderator());

drop policy if exists top5_evaluations_select on public.top5_evaluations;
create policy top5_evaluations_select on public.top5_evaluations
    for select
    using (
        evaluator_id = auth.uid()
        or public.is_moderator()
        or exists (
            select 1
            from public.top5_applications a
            where a.id = application_id
              and a.applicant_id = auth.uid()
              and a.status in ('approved', 'converted', 'rejected', 'cancelled')
        )
    );

drop policy if exists top5_evaluations_insert on public.top5_evaluations;
create policy top5_evaluations_insert on public.top5_evaluations
    for insert
    with check (evaluator_id = auth.uid());

drop policy if exists top5_evaluations_update on public.top5_evaluations;
create policy top5_evaluations_update on public.top5_evaluations
    for update
    using (evaluator_id = auth.uid())
    with check (evaluator_id = auth.uid());

drop policy if exists top5_expert_requests_select on public.top5_expert_requests;
create policy top5_expert_requests_select on public.top5_expert_requests
    for select
    using (
        expert_id = auth.uid()
        or public.is_moderator()
        or public.top5_can_view_application(application_id)
    );

drop policy if exists top5_expert_requests_insert on public.top5_expert_requests;
create policy top5_expert_requests_insert on public.top5_expert_requests
    for insert
    with check (public.is_moderator());

drop policy if exists top5_expert_requests_update on public.top5_expert_requests;
create policy top5_expert_requests_update on public.top5_expert_requests
    for update
    using (expert_id = auth.uid() or public.is_moderator())
    with check (expert_id = auth.uid() or public.is_moderator());

drop policy if exists top5_events_select on public.top5_events;
create policy top5_events_select on public.top5_events
    for select
    using (
        public.is_moderator()
        or actor_id = auth.uid()
        or exists (
            select 1
            from public.top5_applications a
            where a.id = application_id
              and a.applicant_id = auth.uid()
              and a.status in ('approved', 'converted', 'rejected', 'cancelled')
        )
    );

-- +--------------------------------------------------+
-- | 7. Гранты                                          |
-- +--------------------------------------------------+

revoke all on public.top5_applications from anon, authenticated;
revoke all on public.top5_evaluations from anon, authenticated;
revoke all on public.top5_expert_requests from anon, authenticated;
revoke all on public.top5_events from anon, authenticated;
revoke all on public.achievement_temporary_ratings from anon, authenticated;

grant select, insert, update on public.top5_applications to authenticated;
grant select, insert, update on public.top5_evaluations to authenticated;
grant select, insert, update on public.top5_expert_requests to authenticated;
grant select on public.top5_events to authenticated;

grant usage, select on sequence public.top5_applications_id_seq to authenticated;
grant usage, select on sequence public.top5_evaluations_id_seq to authenticated;
grant usage, select on sequence public.top5_expert_requests_id_seq to authenticated;

grant all on public.top5_applications to service_role;
grant all on public.top5_evaluations to service_role;
grant all on public.top5_expert_requests to service_role;
grant all on public.top5_events to service_role;
grant all on public.achievement_temporary_ratings to service_role;
grant all on sequence public.top5_applications_id_seq to service_role;
grant all on sequence public.top5_evaluations_id_seq to service_role;
grant all on sequence public.top5_expert_requests_id_seq to service_role;
grant all on sequence public.top5_events_id_seq to service_role;

-- +--------------------------------------------------+
-- | 8. RPC временных рейтингов                         |
-- +--------------------------------------------------+

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

    -- Кэш всегда соответствует последнему расчёту приложения: если заявка
    -- перестала быть approved или набрала 3 оценки, её строки не возвращаются.
    delete from public.achievement_temporary_ratings;

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
      and r.rating is not null;

    get diagnostics v_inserted = row_count;
    return v_inserted;
end;
$$;

revoke execute on function public.apply_temporary_ratings(jsonb)
    from public, anon, authenticated;
grant execute on function public.apply_temporary_ratings(jsonb)
    to service_role;

-- +--------------------------------------------------+
-- | 9. Эффективный публичный топ                       |
-- +--------------------------------------------------+
--
-- Вьюха отдаёт только published и только публичные поля. display_rank
-- нумерует ranked + временные строки; предварительные строки без временного
-- рейтинга остаются с rank = null и не занимают номер. temp-строка получает
-- тот же слот в общей нумерации, сдвигая постоянные вниз, но её флаг
-- is_temporary отделяет её от постоянных позиций.
--
-- Вьюха принадлежит postgres, поэтому RLS базовых таблиц не применяется;
-- это осознанно: наружу попадают лишь published-строки и публичные поля
-- профиля/категории. Запись в achievements_temporary_ratings по-прежнему
-- доступна только service_role через RPC.

drop function if exists public.user_completed_achievements(uuid);
drop view if exists public.public_achievements;

create view public.public_achievements as
with base as (
    select a.id,
           a.title,
           a.description,
           a.requirements,
           a.category_id,
           a.creator_id,
           a.status,
           a.ranking_status,
           a.average_position,
           a.rank as permanent_rank,
           a.rank_order,
           a.evaluation_count,
           a.created_at,
           a.updated_at,
           a.bt_strength,
           a.bt_rating,
           a.bt_comparisons,
           a.bt_updated_at,
           a.bt_rating_se,
           a.bt_rating_ci_low,
           a.bt_rating_ci_high,
           case
               when cat.id is null then null
               else jsonb_build_object(
                   'id', cat.id,
                   'name', cat.name,
                   'slug', cat.slug
               )
           end as category,
           jsonb_build_object(
               'id', p.id,
               'username', p.username,
               'display_name', p.display_name,
               'avatar_url', p.avatar_url
           ) as creator
    from public.achievements a
    left join public.categories cat on cat.id = a.category_id
    join public.profiles p on p.id = a.creator_id
    where a.status = 'published'
),
temporary as (
    select tr.achievement_id,
           tr.application_id,
           tr.strength,
           tr.rating,
           tr.evaluator_count,
           tr.ordinary_evaluation_count,
           tr.computed_at
    from public.achievement_temporary_ratings tr
    join public.top5_applications app
      on app.id = tr.application_id
     and app.status = 'approved'
),
joined as (
    select b.*,
           (t.achievement_id is not null and b.evaluation_count < 3) as is_temporary,
           case
               when t.achievement_id is not null and b.evaluation_count < 3
               then t.strength
           end as temporary_strength,
           case
               when t.achievement_id is not null and b.evaluation_count < 3
               then t.rating
           end as temporary_rating,
           t.evaluator_count as temporary_evaluator_count,
           coalesce(
               case
                   when b.evaluation_count < 3 then t.strength
               end,
               b.bt_strength
           ) as sort_strength,
           (b.evaluation_count >= 1 or t.achievement_id is not null) as in_leaderboard
    from base b
    left join temporary t on t.achievement_id = b.id
),
numbered as (
    select j.*,
           case
               when j.ranking_status = 'ranked' or j.is_temporary then
                   row_number() over (
                       partition by (j.ranking_status = 'ranked' or j.is_temporary)
                       order by j.sort_strength desc nulls last,
                                j.average_position asc nulls last,
                                j.rank_order asc nulls last,
                                j.id asc
                   )
           end as display_rank
    from joined j
)
select n.id,
       n.title,
       n.description,
       n.requirements,
       n.category_id,
       n.creator_id,
       n.status,
       n.ranking_status,
       n.average_position,
       n.permanent_rank,
       n.display_rank as rank,
       n.display_rank,
       n.is_temporary,
       n.in_leaderboard,
       n.rank_order,
       n.evaluation_count,
       n.created_at,
       n.updated_at,
       case when n.is_temporary then null else n.bt_comparisons end
           as bt_comparisons,
       case when n.is_temporary then null else n.bt_updated_at end
           as bt_updated_at,
       case when n.is_temporary then null else n.bt_rating_se end
           as bt_rating_se,
       case when n.is_temporary then null else n.bt_rating_ci_low end
           as bt_rating_ci_low,
       case when n.is_temporary then null else n.bt_rating_ci_high end
           as bt_rating_ci_high,
       coalesce(n.temporary_strength, n.bt_strength) as bt_strength,
       coalesce(n.temporary_rating, n.bt_rating) as bt_rating,
       n.sort_strength,
       n.temporary_evaluator_count,
       n.category,
       n.creator
from numbered n;

revoke all on public.public_achievements from anon, authenticated;
grant select on public.public_achievements to anon, authenticated;

-- +--------------------------------------------------+
-- | 10. Профиль: эффективное место в списке            |
-- +--------------------------------------------------+

create function public.user_completed_achievements(p_user_id uuid)
returns table (
    achievement_id bigint,
    title text,
    rank integer,
    permanent_rank integer,
    ranking_status public.ranking_status,
    bt_rating double precision,
    bt_strength double precision,
    evaluation_count integer,
    category_name text,
    category_slug text,
    approved_at timestamptz,
    proofs jsonb,
    is_temporary boolean
)
language sql
stable
security definer
set search_path = public
as $$
    select a.id,
           a.title,
           a.rank::integer,
           a.permanent_rank::integer,
           a.ranking_status,
           a.bt_rating,
           a.bt_strength,
           a.evaluation_count,
           cat.name,
           cat.slug,
           c.approved_at,
           coalesce(
               jsonb_agg(
                   jsonb_build_object(
                       'id', pr.id,
                       'proof_type', pr.proof_type,
                       'description', pr.description,
                       'storage_path', pr.storage_path,
                       'created_at', pr.created_at
                   )
                   order by pr.created_at, pr.id
               ) filter (
                   where pr.id is not null
                     and (
                         pr.proof_type = 'link'
                         or pr.storage_path like c.user_id::text || '/%'
                     )
               ),
               '[]'::jsonb
           ),
           a.is_temporary
    from public.achievement_completions c
    join public.public_achievements a
      on a.id = c.achievement_id
     and a.status = 'published'
    left join public.categories cat on cat.id = a.category_id
    left join public.proofs pr on pr.completion_id = c.id
    where c.user_id = p_user_id
      and c.status = 'approved'
    group by a.id, a.title, a.rank, a.permanent_rank, a.ranking_status,
             a.bt_rating, a.bt_strength, a.evaluation_count, a.is_temporary,
             cat.name, cat.slug, c.approved_at, c.user_id
    order by a.rank asc nulls last, c.approved_at desc nulls last, a.id asc
    limit 500;
$$;

revoke execute on function public.user_completed_achievements(uuid) from public;
grant execute on function public.user_completed_achievements(uuid)
    to anon, authenticated;

notify pgrst, 'reload schema';
