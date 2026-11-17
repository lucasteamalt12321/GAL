-- GAL — 031: заявка в топ-5 вместе с выполнением достижения
-- 031_top5_completion_claims.sql
--
-- Поток подачи заявки в топ-5 переработан: заявка отправляется вместе с
-- выполнением достижения (чекбокс в форме выполнения) ЛЮБЫМ пользователем,
-- который отправляет выполнение, а не только автором достижения. Заявка
-- связывается со своим выполнением через колонку completion_id; доказательство
-- заявки — это proof выполнения, поэтому обязательное поле evidence_url
-- становится необязательным.
--
-- Здесь:
--   1. Колонка completion_id (FK на achievement_completions, on delete set null)
--      и частичный индекс по заполненным значениям.
--   2. Ослабление evidence_url: drop not null + scheme-constraint допускает
--      null и пустую строку. Length-constraint не трогаем.
--   3. Backfill старых активных заявок: у автора достижения с одобренным
--      выполнением проставляем completion_id (unique (achievement_id, user_id)
--      гарантирует не более одного совпадения).
--   4. top5_application_guard: INSERT разрешает автору достижения ИЛИ владельцу
--      выполнения, указанного в completion_id; UPDATE запрещает менять
--      completion_id (TARGET_IMMUTABLE). Остальная логика 027 без изменений,
--      код ошибки NOT_AUTHOR сохранён — сервис на него маппится.
--   5. RLS insert-политика повторяет новую логику guard.
--
-- apply_temporary_ratings, public_achievements, триггеры конвертации,
-- экспертные таблицы и гранты не меняются: существующие гранты покрывают
-- новую колонку.

-- +--------------------------------------------------+
-- | 1. Связь заявки с выполнением                      |
-- +--------------------------------------------------+

alter table public.top5_applications
    add column if not exists completion_id bigint
        references public.achievement_completions (id) on delete set null;

comment on column public.top5_applications.completion_id is
    'Выполнение достижения, вместе с которым подана заявка; proof выполнения служит доказательством заявки.';

create index if not exists top5_applications_completion_idx
    on public.top5_applications (completion_id)
    where completion_id is not null;

-- +--------------------------------------------------+
-- | 2. evidence_url становится необязательным          |
-- +--------------------------------------------------+

alter table public.top5_applications
    alter column evidence_url drop not null;

alter table public.top5_applications
    drop constraint if exists top5_applications_evidence_scheme;
alter table public.top5_applications
    add constraint top5_applications_evidence_scheme
        check (evidence_url is null or evidence_url = '' or evidence_url ~* '^https?://[^[:space:]]+$');

-- +--------------------------------------------------+
-- | 3. Backfill старых активных заявок                 |
-- +--------------------------------------------------+

update public.top5_applications a
set completion_id = c.id
from public.achievement_completions c
where a.completion_id is null
  and a.status in ('pending', 'expert_review')
  and c.achievement_id = a.achievement_id
  and c.user_id = a.applicant_id
  and c.status = 'approved';

-- +--------------------------------------------------+
-- | 4. Guard: автор или владелец выполнения            |
-- +--------------------------------------------------+

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
            -- Заявку подаёт автор достижения или пользователь, отправляющий
            -- выполнение этого достижения (completion_id принадлежит ему).
            if v_actor is null or not (
                v_creator = v_actor
                or (
                    new.completion_id is not null
                    and exists (
                        select 1
                        from public.achievement_completions c
                        where c.id = new.completion_id
                          and c.user_id = v_actor
                          and c.achievement_id = new.achievement_id
                    )
                )
            ) then
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
       or new.applicant_id is distinct from old.applicant_id
       or new.completion_id is distinct from old.completion_id then
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

-- +--------------------------------------------------+
-- | 5. RLS: insert автору или владельцу выполнения     |
-- +--------------------------------------------------+

drop policy if exists top5_applications_insert on public.top5_applications;
create policy top5_applications_insert on public.top5_applications
    for insert
    with check (
        applicant_id = auth.uid()
        and exists (
            select 1
            from public.achievements a
            where a.id = achievement_id
              and a.status = 'published'
              and (
                  a.creator_id = auth.uid()
                  or exists (
                      select 1
                      from public.achievement_completions c
                      where c.id = completion_id
                        and c.user_id = auth.uid()
                        and c.achievement_id = a.id
                  )
              )
        )
    );

notify pgrst, 'reload schema';