-- GAL — 034: заявка в топ-5 при создании достижения
-- 034_creator_top5_claim.sql
--
-- Автор достижения должен иметь право подать заявку в топ-5 вместе с
-- созданием самого достижения: раньше форма создания не содержала полей
-- заявки, а guard/RLS требовали status='published', поэтому заявку автора
-- нельзя было подать в принципе (у автора уже есть собственное прохождение
-- верификации, второе прохождение запрещено unique (achievement_id, user_id)).
--
-- Здесь:
--   1. top5_application_guard: INSERT разрешён автору достижения в статусе
--      'pending' — привязка к собственному прохождению (completion_id) и
--      applicant_id = auth.uid(). Для любых других достижений и статусов
--      правила прежние: 'published' + автор ИЛИ владелец прохождения.
--   2. RLS insert-политика повторяет новую логику guard: для 'pending'
--      достижения заявку может создать только его автор и только со своим
--      прохождением.
--   3. Авторские заявки на неопубликованные достижения видны только автору
--      и модераторам — политики чтения не менялись.
--
-- Экспертная оценка не участвует: заявку рассматривает модератор, временное
-- место выставляет он (approved_top5 / approved / rejected). Таблицы
-- top5_expert_requests и top5_evaluations не меняются.
--
-- Порядок применения: после 031 (колонка completion_id и обновлённый guard).

-- +--------------------------------------------------+
-- | 1. Guard: автор может подать заявку на своё        |
-- |    ещё не опубликованное достижение                |
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

        if v_ach_status = 'pending' then
            -- Заявка на ещё не опубликованное достижение подаётся только его
            -- автором и только со своим собственным прохождением: доказательство
            -- заявки — proof прохождения.
            if v_actor is null or v_creator is distinct from v_actor then
                raise exception 'ACHIEVEMENT_NOT_PUBLISHED';
            end if;
            if new.completion_id is null or not exists (
                select 1
                from public.achievement_completions c
                where c.id = new.completion_id
                  and c.user_id = v_actor
                  and c.achievement_id = new.achievement_id
            ) then
                raise exception 'NOT_AUTHOR';
            end if;
        elsif v_ach_status <> 'published' then
            raise exception 'ACHIEVEMENT_NOT_PUBLISHED';
        end if;

        if v_count >= 3 then
            raise exception 'ALREADY_PERMANENT';
        end if;
        if v_rank is not null and v_rank <= 5 then
            raise exception 'ALREADY_IN_TOP5';
        end if;

        if not v_privileged then
            -- Заявку подаёт автор достижения или пользователь, отправивший
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
-- | 2. RLS: авторская заявка на pending-достижение     |
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
              and (
                  (
                      a.status = 'published'
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
                  or (
                      a.status = 'pending'
                      and a.creator_id = auth.uid()
                      and exists (
                          select 1
                          from public.achievement_completions c
                          where c.id = completion_id
                            and c.user_id = auth.uid()
                            and c.achievement_id = a.id
                      )
                  )
              )
        )
    );

notify pgrst, 'reload schema';
