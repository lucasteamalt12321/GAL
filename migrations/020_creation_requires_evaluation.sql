-- 020_creation_requires_evaluation.sql
--
-- Новый пользователь может предложить своё достижение только после того,
-- как прошёл и оценил хотя бы одно опубликованное достижение.
--
-- Роутер и сервис уже проверяют это правило, но user-JWT можно использовать
-- и напрямую против PostgREST. Поэтому правило закрепляется в RLS:
-- insert в achievements разрешён только при наличии такой оценки.
-- Service role (скрипты, seed, модерация) обходит RLS и может создавать
-- достижения без этого условия.

create or replace function public.user_has_published_evaluation(p_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.evaluations e
        join public.achievements a on a.id = e.achievement_id
        where e.user_id = p_user_id
          and a.status = 'published'
    );
$$;

-- Supabase выдаёт EXECUTE на новые функции в public ролям anon/authenticated
-- через default privileges, поэтому одного `revoke ... from public` мало:
-- у anon остаётся явный grant. Отзываем отдельно.
revoke all on function public.user_has_published_evaluation(uuid) from public, anon;
grant execute on function public.user_has_published_evaluation(uuid) to authenticated;

drop policy if exists achievements_insert_own on public.achievements;
create policy achievements_insert_own on public.achievements
    for insert
    with check (
        creator_id = auth.uid()
        and status = 'pending'
        and public.user_has_published_evaluation(auth.uid())
    );
