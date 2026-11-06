-- GAL — 016: автор заявки видит причину её отклонения
-- 016_completion_review_visibility.sql
--
-- moderation_reviews были видны только модераторам. Для раздела «Мои заявки»
-- и формы переотправки автору нужна причина отклонения его собственной заявки.

drop policy if exists moderation_reviews_select_own on public.moderation_reviews;
create policy moderation_reviews_select_own on public.moderation_reviews
    for select
    using (
        exists (
            select 1
            from public.achievement_completions c
            where c.id = moderation_reviews.completion_id
              and c.user_id = auth.uid()
        )
    );
