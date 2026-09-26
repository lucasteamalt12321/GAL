-- GAL — retry & cleanup completion lifecycle
-- 009_retry_completion_rpc.sql
-- M2/M3: после отклонения (rejected) заявка на выполнение должна быть повторно
-- подаваемой, а неудачная привязка доказательства не должна оставлять
-- «сиротскую» pending-заявку без proof.
-- Прямого DELETE/UPDATE у authenticated на achievement_completions/proofs нет
-- (RLS), поэтому обе операции выполняются security definer функциями.

-- 1. Сброс отклонённой заявки: удалить старые proof-строки и вернуть статус
--    в pending. Row-удаление cascade не затронуто (proofs FK on delete cascade —
--    здесь удаляем строки proofs напрямую).
create or replace function public.reset_rejected_completion(p_achievement_id bigint)
returns table (
    completion_id bigint,
    status public.completion_status
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_cid bigint;
    v_cur_status public.completion_status;
begin
    if v_uid is null then
        raise exception 'AUTH_REQUIRED';
    end if;

    select c.id, c.status
    into v_cid, v_cur_status
    from public.achievement_completions c
    where c.achievement_id = p_achievement_id
      and c.user_id = v_uid;

    if v_cid is null then
        raise exception 'NO_COMPLETION';
    end if;

    if v_cur_status <> 'rejected' then
        raise exception 'NOT_REJECTED';
    end if;

    delete from public.proofs
    where completion_id = v_cid;

    update public.achievement_completions
    set status = 'pending',
        approved_at = null
    where id = v_cid;

    return query
        select v_cid, 'pending'::public.completion_status;
end;
$$;

-- 2. Удаление свежесозданной пустой pending-заявки (rollback attach'а),
--    только если это действительно заявка текущего пользователя, ещё pending
--    и без единого proof — чтобы не затронуть реальные заявки.
create or replace function public.remove_empty_pending_completion(p_completion_id bigint)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
begin
    if v_uid is null then
        raise exception 'AUTH_REQUIRED';
    end if;

    delete from public.achievement_completions c
    where c.id = p_completion_id
      and c.user_id = v_uid
      and c.status = 'pending'
      and not exists (
          select 1
          from public.proofs p
          where p.completion_id = c.id
      );

    return found;
end;
$$;

revoke execute on function public.reset_rejected_completion(bigint) from public;
grant execute on function public.reset_rejected_completion(bigint) to authenticated;

revoke execute on function public.remove_empty_pending_completion(bigint) from public;
grant execute on function public.remove_empty_pending_completion(bigint) to authenticated;