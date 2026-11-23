-- GAL — 033: пароль LTHub-аккаунта в GAL
-- 033_lthub_password.sql
--
-- Для аккаунта, созданного через OAuth LTHub, GAL больше не заводит собственный
-- служебный пароль: пароль аккаунта — это пароль в LTHub. Чтобы GAL мог проверить
-- его у себя, нужны две вещи:
--
--   1. lthub_login — логин пользователя в LTHub. Без него нельзя обратиться к
--      API LTHub (/api/auth/login) и проверить пароль. Заполняется при каждом
--      OAuth-входе: LTHub отдаёт login в userinfo, а он может измениться.
--
--   2. password_synced — флаг «пароль служебного Supabase-пользователя =
--      пользовательский». Пока флаг снят, пароль — служебный токен, и вход через
--      LTHub может его перевыпустить (как раньше). Как только пользовательский
--      пароль подтверждён (вход по паролю LTHub или смена пароля в GAL), флаг
--      поднимается, и OAuth-вход больше не затирает пароль: иначе пароль LTHub
--      перестал бы работать после каждого входа через LTHub.
--
-- Таблица доступна только service_role (как и в 015) — правки не меняются.

alter table public.external_identities
    add column if not exists lthub_login text;

comment on column public.external_identities.lthub_login is
    'Логин пользователя в LTHub — используется для проверки пароля LTHub-аккаунта через API LTHub.';

alter table public.external_identities
    add column if not exists password_synced boolean not null default false;

comment on column public.external_identities.password_synced is
    'Пароль служебного Supabase-пользователя подтверждён пользователем (не служебный токен) — OAuth-вход его не перевыпускает.';
