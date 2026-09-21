# Active Context — GAL

## Текущий фокус

D1, D2 и D3 завершены: каркас FastAPI развёрнут на проде (`https://gal-inky.vercel.app`), миграции `001_init.sql` + `002_rls.sql` применены к Supabase `wiwyitafoprxmlyndkwe`, аутентификация (cookie-сессия Supabase Auth, login/register/logout/recover/me) реализована и проверена. Следующий блок — D4 (Achievements CRUD): создание/просмотр достижений, страницы категорий, личный профиль.

## Статус задач

- D3 (Authentication): **completed**
  - [x] `app/templating.py` — общий `Jinja2Templates`
  - [x] `app/services/auth.py` — cookie-сессия, `resolve_user`, refresh, `CurrentUser`
  - [x] `app/dependencies.py` — `get_current_user` / `get_current_moderator` / `get_current_admin`
  - [x] `app/middleware.py` — `UserContextMiddleware` (`request.state.user`)
  - [x] `app/routers/auth.py` — login/register/logout/recover/me
  - [x] Шаблоны `auth/login.html`, `auth/register.html`, `auth/recover.html`; навигация в `base.html`
  - [x] Тесты `tests/test_auth.py` (6); живая проверка против Supabase
- D2 (Database Schema + RLS): **completed**
  - [x] `001_init.sql` + `002_rls.sql` написаны и согласованы
  - [x] Миграции применены к проду через Management API (PAT)
  - [x] Проверено на проде: 8 таблиц, 9 категорий, RLS на всех таблицах, триггеры активны
- D1 (Foundation + Memory Bank): **completed**
  - [x] Каркас FastAPI, health, homepage, тесты, README
  - [x] Деплой на Vercel: `/health` = `configured: true`, `environment: production`
- D4 (Achievements CRUD): **pending**

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
14. **Auth-сессия:** Supabase access+refresh токены в httpOnly-cookie `gal_session`/`gal_refresh` (SameSite=Lax, `secure` в проде, 30 дней); `UserContextMiddleware` резолвит пользователя, при истечении access-токена делает refresh и переписывает cookie; `/static` и `/health` пропускаются. Профиль читается anon-клиентом (RLS `profiles_select` публичный).
15. **Обработка ошибок auth:** ловятся конкретные типы (`AuthError`, `httpx.HTTPError`, `APIError`, `SupabaseNotConfiguredError`) — без blind `except Exception`.

## Открытые вопросы (не блокируют MVP)

- `?` Score политика → MVP: 0 очков за `unknown` достижения.
- Первое достижение пользователя (нет personal scale для сравнения) → планируется разрешить оценку относительно глобальной шкалы как исключение.
- Вес оценок по опыту → будущие версии.
- Включена ли email-конфирмация при регистрации: код обрабатывает оба случая (session есть → сразу вход; session нет → показ сообщения «подтвердите email»). Точный статус настройки Supabase ещё не подтверждён визуально.
- Уникальность `username` при регистрации: Supabase не проверяет до `handle_new_user`; при коллизии возможна ошибка на уровне БД (обработать в D10).

## Следующие шаги

1. D4 — Achievements CRUD: `app/routers/achievements.py` (список/деталь/создание), `app/services/achievements.py`, шаблоны `achievements/`, подключение категорий из БД, страница профиля пользователя.

## Риски на горизонте

- Ранкинг чувствителен к tie-breaking и атомарности пересчёта → покрыть тестами раньше всего (D7).
- CSRF для форм: MVP — SameSite=Lax + серверная логика; полноценный CSRF-токен на D10.
- Создание achievement требует service-role или user-JWT клиента (`new_user_client`) — не забыть проставить сессию для записи под RLS (D4).