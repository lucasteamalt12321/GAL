-- GAL — 035: комментарий к прохождению и несколько файлов
-- 035_completion_comment.sql
--
-- Пользователь прикладывает к прохождению несколько файлов (суммарный лимит
-- задаёт приложение, MAX_ATTACHMENT_BYTES) и пишет один комментарий ко всей
-- заявке. Комментарий принадлежит документу прохождения, а не отдельному
-- файлу, поэтому он хранится в achievement_completions.comment и показывается
-- наравне с доказательствами — с тем же правилом видимости.
--
-- 1. Колонка achievement_completions.comment (+ ограничение длины).
-- 2. RPC set_completion_comment — запись комментария владельцем pending-заявки:
--    под RLS пользователь не может обновлять achievement_completions.
-- 3. RPC remove_completion_proofs — откат доказательств заявки, если часть
--    вложений загрузилась, а часть — нет.
-- 4. Публичные RPC achievement_completers и user_completed_achievements
--    пересозданы с колонкой comment: у скрытых доказательств скрыт и
--    комментарий.

-- Файл применяется одной транзакцией: между drop и create публичных RPC
-- функции не должно не существовать.
begin;

-- +--------------------------------------------------+
-- | 1. Комментарий к прохождению                      |
-- +--------------------------------------------------+

alter table public.achievement_completions
    add column if not exists comment text;

comment on column public.achievement_completions.comment is
    'Комментарий пользователя ко всей заявке на прохождение (до 2000 символов).';

alter table public.achievement_completions
    drop constraint if exists achievement_completions_comment_length;

alter table public.achievement_completions
    add constraint achievement_completions_comment_length
    check (comment is null or char_length(comment) <= 2000);

-- +--------------------------------------------------+
-- | 2. Запись комментария владельцем заявки           |
-- +--------------------------------------------------+

create or replace function public.set_completion_comment(
    p_completion_id bigint,
    p_comment text
)
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

    -- Комментарий можно поменять только пока заявка ждёт модератора:
    -- после решения он становится частью истории проверки.
    update public.achievement_completions c
       set comment = nullif(left(coalesce(p_comment, ''), 2000), '')
     where c.id = p_completion_id
       and c.user_id = v_uid
       and c.status = 'pending';

    return found;
end;
$$;

revoke execute on function public.set_completion_comment(bigint, text)
    from public, anon, authenticated, service_role;
grant execute on function public.set_completion_comment(bigint, text) to authenticated;

-- +--------------------------------------------------+
-- | 3. Откат частично загруженных доказательств        |
-- +--------------------------------------------------+

-- Заявка может остаться с половиной вложений, если один из файлов не
-- загрузился. Доказательства такой заявки удаляются, а сама заявка остаётся
-- pending — пользователь прикладывает файлы заново. Саму заявку удалять
-- нельзя: на неё ссылается moderation_reviews.
-- Удаляются только перечисленные доказательства (p_proof_ids) — ранее
-- приложенные к этой же заявке файлы остаются на месте.
create or replace function public.remove_completion_proofs(
    p_completion_id bigint,
    p_proof_ids bigint[]
)
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

    if p_proof_ids is null or cardinality(p_proof_ids) = 0 then
        return false;
    end if;

    delete from public.proofs pr
     where pr.completion_id = p_completion_id
       and pr.id = any(p_proof_ids)
       and exists (
           select 1
           from public.achievement_completions c
           where c.id = pr.completion_id
             and c.user_id = v_uid
             and c.status = 'pending'
       );

    return found;
end;
$$;

revoke execute on function public.remove_completion_proofs(bigint, bigint[])
    from public, anon, authenticated, service_role;
grant execute on function public.remove_completion_proofs(bigint, bigint[])
    to authenticated;

-- +--------------------------------------------------+
-- | 4. Публичные списки: доказательства + комментарий |
-- +--------------------------------------------------+

drop function if exists public.achievement_completers(bigint);

create function public.achievement_completers(p_achievement_id bigint)
returns table (
    user_id uuid,
    username text,
    display_name text,
    avatar_url text,
    approved_at timestamptz,
    proofs jsonb,
    comment text
)
language sql
stable
security definer
set search_path = public
as $$
    select p.id,
           p.username,
           p.display_name,
           p.avatar_url,
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
                     and (
                         c.proofs_visible
                         or c.user_id = auth.uid()
                         or public.is_moderator()
                     )
               ),
               '[]'::jsonb
           ),
           case
               when c.proofs_visible
                 or c.user_id = auth.uid()
                 or public.is_moderator()
               then c.comment
               else null
           end
    from public.achievement_completions c
    join public.achievements a
      on a.id = c.achievement_id
     and a.status = 'published'
    join public.profiles p on p.id = c.user_id
    left join public.proofs pr on pr.completion_id = c.id
    where c.achievement_id = p_achievement_id
      and c.status = 'approved'
    group by p.id, p.username, p.display_name, p.avatar_url, c.approved_at,
             c.user_id, c.proofs_visible, c.comment
    order by c.approved_at asc nulls last, p.username asc
    limit 500;
$$;

revoke execute on function public.achievement_completers(bigint) from public;
grant execute on function public.achievement_completers(bigint)
    to anon, authenticated;

drop function if exists public.user_completed_achievements(uuid);

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
    is_temporary boolean,
    comment text
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
                     and (
                         c.proofs_visible
                         or c.user_id = auth.uid()
                         or public.is_moderator()
                     )
               ),
               '[]'::jsonb
           ),
           a.is_temporary,
           case
               when c.proofs_visible
                 or c.user_id = auth.uid()
                 or public.is_moderator()
               then c.comment
               else null
           end
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
             cat.name, cat.slug, c.approved_at, c.user_id, c.proofs_visible,
             c.comment
    order by a.rank asc nulls last, c.approved_at desc nulls last, a.id asc
    limit 500;
$$;

revoke execute on function public.user_completed_achievements(uuid) from public;
grant execute on function public.user_completed_achievements(uuid)
    to anon, authenticated;

notify pgrst, 'reload schema';

commit;
