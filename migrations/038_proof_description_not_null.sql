-- GAL — 038: исправление отправки прохождения с пустым описанием доказательства
--
-- Сбой: после 036 RPC submit_completion_with_proofs писал в proofs.description
-- значение nullif(btrim(...), '') — то есть NULL, когда описание не заполнено.
-- Колонка public.proofs.description объявлена NOT NULL DEFAULT '', поэтому любая
-- заявка без описания доказательства падала с SQLSTATE 23502
-- (null value in column "description" ... violates not-null constraint).
-- Приложение этот код не распознавало и показывало безликое
-- «Не удалось отправить заявку. Попробуйте ещё раз.»
--
-- Доказательство: 2026-11-26, достижение «Программист python» — откат при
-- создании, в таблице proofs последняя успешная вставка осталась от 2026-11-24,
-- то есть до деплоя D25 (миграция 036).
--
-- Исправление: пустое описание сохраняем как пустую строку. NOT NULL при этом
-- соблюдается, а отпечаток (fingerprint) считает '' и '' одинаково — на
-- дубликаты это не влияет.

begin;

create or replace function public.submit_completion_with_proofs(
    p_user_id uuid,
    p_achievement_id bigint,
    p_harder_count integer default null,
    p_proofs_visible boolean default true,
    p_comment text default '',
    p_proofs jsonb default '[]'::jsonb
)
returns table (
    completion_id bigint,
    attempt_no integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_completion_id bigint;
    v_status public.completion_status;
    v_attempt integer;
    v_creator uuid;
    v_achievement_status public.achievement_status;
    v_completion public.achievement_completions%rowtype;
begin
    if auth.role() <> 'service_role' then
        raise exception using errcode = '42501', message = 'SYSTEM_ONLY';
    end if;
    if p_user_id is null or p_achievement_id is null then
        raise exception using errcode = '22023', message = 'SUBMISSION_FIELDS_REQUIRED';
    end if;
    if p_proofs is null or jsonb_typeof(p_proofs) <> 'array'
        or jsonb_array_length(p_proofs) = 0 then
        raise exception using errcode = '22023', message = 'PROOF_REQUIRED';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended(p_user_id::text || ':' || p_achievement_id::text, 0)
    );

    select a.creator_id, a.status
    into v_creator, v_achievement_status
    from public.achievements a
    where a.id = p_achievement_id;
    if not found then
        raise exception using errcode = '23503', message = 'ACHIEVEMENT_NOT_FOUND';
    end if;
    if v_achievement_status <> 'published'
        and not (
            v_achievement_status = 'pending'
            and v_creator = p_user_id
        ) then
        raise exception using errcode = '42501', message = 'NOT_ALLOWED';
    end if;

    select c.id, c.status
    into v_completion_id, v_status
    from public.achievement_completions c
    where c.achievement_id = p_achievement_id
      and c.user_id = p_user_id
    for update;

    if found then
        if v_status = 'rejected' then
            null;
        elsif v_status = 'pending' and not exists (
            select 1 from public.proofs p where p.completion_id = v_completion_id
        ) then
            null;
        else
            raise exception using errcode = '23505', message = 'ALREADY_COMPLETED';
        end if;
    else
        insert into public.achievement_completions (
            achievement_id,
            user_id,
            status,
            requested_harder_count,
            proofs_visible,
            comment,
            submission_attempt
        )
        values (
            p_achievement_id,
            p_user_id,
            'pending',
            p_harder_count,
            p_proofs_visible,
            nullif(left(coalesce(p_comment, ''), 2000), ''),
            0
        )
        returning id into v_completion_id;
    end if;

    v_attempt := coalesce((
        select c.submission_attempt
        from public.achievement_completions c
        where c.id = v_completion_id
    ), 0) + 1;

    update public.achievement_completions c
    set status = 'pending',
        approved_at = null,
        requested_harder_count = case
            when p_harder_count is null then c.requested_harder_count
            else p_harder_count
        end,
        proofs_visible = p_proofs_visible,
        comment = nullif(left(coalesce(p_comment, ''), 2000), ''),
        submission_attempt = v_attempt
    where c.id = v_completion_id;

    delete from public.proofs p where p.completion_id = v_completion_id;

    insert into public.proofs (
        completion_id,
        storage_path,
        proof_type,
        description,
        content_hash
    )
    select
        v_completion_id,
        btrim(item->>'storage_path'),
        coalesce(item->>'proof_type', 'other')::public.proof_type,
        -- proofs.description NOT NULL: пустое описание сохраняем пустой строкой.
        coalesce(nullif(btrim(coalesce(item->>'description', '')), ''), ''),
        case
            when coalesce(item->>'proof_type', 'other') = 'link' then null
            else nullif(item->>'content_hash', '')
        end
    from jsonb_array_elements(p_proofs) as item
    order by item->>'storage_path';

    select c.* into v_completion
    from public.achievement_completions c
    where c.id = v_completion_id;

    perform public.register_submission_fingerprint(
        'completion',
        p_user_id,
        v_completion_id,
        v_attempt,
        public.completion_submission_payload(v_completion)
    );

    return query select v_completion_id, v_attempt;
end;
$$;

revoke execute on function public.submit_completion_with_proofs(
    uuid, bigint, integer, boolean, text, jsonb
) from public, anon, authenticated;
grant execute on function public.submit_completion_with_proofs(
    uuid, bigint, integer, boolean, text, jsonb
) to service_role;

commit;
