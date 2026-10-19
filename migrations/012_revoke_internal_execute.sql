-- GAL — закрыть прямой вызов recompute_ranks_internal
-- 012_revoke_internal_execute.sql
--
-- 010_ranking_integrity.sql делал `revoke execute ... from public`, но Supabase
-- выдаёт execute каждой новой функции через alter default privileges отдельным
-- ролям (anon, authenticated, service_role), поэтому revoke от PUBLIC их не
-- снимает: проверено на проде — вызов `set role anon; select
-- public.recompute_ranks_internal()` выполнялся успешно.
--
-- Функция — security definer без гварда: анонимный клиент мог инициировать
-- глобальный пересчёт рангов сколько угодно раз (DoS-нагрузка, гонки двух
-- фаз). Прямых легитимных вызывающих нет: submit_evaluation() владеет
-- привилегией как owner (postgres), внешний вход только через гвард-
-- обёртку recompute_ranks().
revoke execute on function public.recompute_ranks_internal()
    from anon, authenticated, service_role;
