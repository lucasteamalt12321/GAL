# Active Context — GAL

## Текущий фокус

D1 и D2 завершены: каркас FastAPI развёрнут на проде (`https://gal-inky.vercel.app`), миграции `001_init.sql` + `002_rls.sql` применены к Supabase `wiwyitafoprxmlyndkwe`. Следующий блок — D3 (Authentication): cookie-сессия на Supabase Auth, `dependencies.py`, маршруты login/register/recover, middleware.

## Статус задач

- D2 (Database Schema + RLS): **completed**
  - [x] `001_init.sql` + `002_rls.sql` написаны и согласованы
  - [x] Миграции применены к проду через Management API (PAT)
  - [x] Проверено на проде: 8 таблиц, 9 категорий, RLS на всех таблицах, триггеры активны
- D1 (Foundation + Memory Bank): **completed**
  - [x] Каркас FastAPI, health, homepage, тесты, README
  - [x] Деплой на Vercel: `/health` = `configured: true`, `environment: production`
- D3 (Authentication): **pending**

## Принятые архитектурные решения

1. **Frontend:** Jinja2 + минимальный JS (серверный рендеринг).
2. **Backend:** Python + FastAPI (monolith: один backend + одна БД).
3. **DB/Auth/Storage:** Supabase (PostgreSQL + Auth + Storage).
4. **Доступ к БД:** официальный клиент `supabase` (PostgREST): anon для пользовательских операций + user JWT, service role только для привилегированных серверных операций; RLS не обходится без необходимости.
5. **Deploy:** Vercel (ASGI `app.main:app`); PAT `SUPABASE_ACCESS_TOKEN` в `.env` (только Management API/CLI, приложение его не читает).
6. **Draft статус:** отсутствует — только `pending → published` (упрощение MVP).
7. **Creator Evaluation:** включено — создатель автоматически первый completer/evaluater своего достижения.
8. **Дубликаты:** разрешены вручную модератором (без автодетекта в MVP).
9. **Ranking Engine** — отдельный модуль `app/services/ranking.py`, покрытый тестами (D7).
10. **Player Score** — производное значение (`1000 / rank`), не хранится; пересчитывается.
11. **Tie-breaking v1.0:** при равном average порядок фиксируется случайно при разрешении конфликта (не каждый просмотр).
12. **Транзакционность пересчёта ранкинга (§57):** решить на D7 (Postgres-функция через `rpc()` либо контролируемый серверный пересчёт).
13. **RLS:** роль меняется только service role (триггер `prevent_profile_role_change`); оценка допускается только с `approved` completion; locked-оценки не редактируются.

## Открытые вопросы (не блокируют MVP)

- `?` Score политика → MVP: 0 очков за `unknown` достижения.
- Первое достижение пользователя (нет personal scale для сравнения) → планируется разрешить оценку относительно глобальной шкалы как исключение.
- Вес оценок по опыту → будущие версии.
- Включена ли email-конфирмация при регистрации (влияет на D3-flow «подтвердить почту»).

## Следующие шаги

1. D3 — Authentication: `services/auth.py` (resolve_user по cookie), `dependencies.py` (get_current_user/moderator/admin), `routers/auth.py` (login/register/logout/recover), middleware user context, шаблоны login/register, тесты.

## Риски на горизонте

- Ранкинг чувствителен к tie-breaking и атомарности пересчёта → покрыть тестами раньше всего (D7).
- Auth cookie-flow Supabase + FastAPI: аккуратно с рефрешем токена и expiry.
- CSRF для форм: MVP — SameSite=Lax + серверная логика; полноценный CSRF-токен на D10.