# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **25%** (D1–D3 completed; D4–D10 pending).

## Deliverables статус

| ID  | Deliverable                       | Status      |
|-----|-----------------------------------|-------------|
| D1  | Foundation + Memory Bank          | completed   |
| D2  | Database Schema + RLS             | completed   |
| D3  | Authentication                    | completed   |
| D4  | Achievements CRUD                 | pending     |
| D5  | Proofs + Moderation               | pending     |
| D6  | Evaluation Engine + Creator Eval  | pending     |
| D7  | Ranking Engine (тесты)            | pending     |
| D8  | Player Score + Leaderboard        | pending     |
| D9  | Reports                           | pending     |
| D10 | Polish (UI, security, errors)     | pending     |

## Known Issues

- StarletteDeprecationWarning от `starlette.testclient` — warning из библиотеки, не от нашего кода.
- PAT `SUPABASE_ACCESS_TOKEN` хранится в локальном `.env` (не коммитится); в `.env.example` — пустой плейсхолдер.
- Storage bucket `proofs` и `avatars` ещё не созданы (фаза D5).
- Уникальность `username`: проверяется только `profiles_username_key` на уровне БД (Supabase Auth не валидирует); коллизия при регистрации → ошибка `handle_new_user` (обработать в D10).
- `/auth/recover` использует `reset_password_for_email` без `redirect_to` — письмо ведёт на дефолтный URL Supabase; настроить в D10.

## Changelog

### 2026-09-21 — D3 Authentication completed
- `app/templating.py` — общий `Jinja2Templates` (используется в `main.py` и роутерах).
- `app/services/auth.py` — `CurrentUser`, `new_anon_client`/`new_user_client`, cookie-хелперы (`gal_session`/`gal_refresh`, httpOnly, SameSite=Lax, `secure` в проде, 30 дней), `resolve_user` с авто-refresh access-токена; обработка конкретных исключений (`AuthError`, `httpx.HTTPError`, `APIError`).
- `app/dependencies.py` — `get_optional_user`, `get_current_user` (401), `get_current_moderator`/`get_current_admin` (403).
- `app/middleware.py` — `UserContextMiddleware`: `request.state.user`, дозапись обновлённых cookie, пропуск `/static` и `/health`.
- `app/routers/auth.py` — `GET/POST /auth/login`, `/auth/register`, `/auth/recover`, `POST /auth/logout`, `GET /auth/me` (JSON).
- Шаблоны `auth/login.html`, `auth/register.html`, `auth/recover.html`; в `base.html` — состояние пользователя (username/sign out либо sign in/register) и flash-сообщения.
- CSS: `.auth-card`, `.stack`, `.field`, `.flash`, `.btn-link`, `.nav-user`.
- `tests/test_auth.py` — 6 тестов (страницы, 401 на `/auth/me`, logout 303, гостевая навигация). Итого `pytest` — 10 passed; `ruff check` — чисто.
- Живая проверка против Supabase: неверные креды → graceful error-страница; bogus cookie → 401; logout → 303.

### 2026-09-20 — D2 Database Schema + RLS completed
- Создан Supabase-проект `wiwyitafoprxmlyndkwe` (GlobalAchievmentsList).
- В `.env` добавлены: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ACCESS_TOKEN` (PAT для Management API).
- Ключи и `APP_ENV=production` добавлены на Vercel (Secrets). D1 развёрнут: `https://gal-inky.vercel.app` — `/health` показывает `configured: true`, `/` и статика отвечают 200.
- `migrations/002_rls.sql` — RLS: helper-функции `is_moderator()`/`is_admin()`, триггер `prevent_profile_role_change` (роль меняет только service role), политики на все 8 таблиц, grants для `anon`/`authenticated`, `service_role` — все права.
- Миграции `001_init.sql` + `002_rls.sql` применены к проду через Management API (PAT).
- Проверено на проде: 8 таблиц созданы, 9 категорий засеяны, RLS включён на всех таблицах, триггеры `on_auth_user_created`, `set_updated_at`, `prevent_profile_role_change_trigger` активны.

### 2026-09-18 — D1 Foundation completed
- Запушены AGENTS.md, docs/README.md, memory_bank (`9882598`).
- Создан каркас FastAPI: `app/` (`main.py`, `config.py`, `database/connection.py`, `routers/health.py`), `templates/` (base, index), `static/` (css), пустые `services/`, `schemas/`.
- `config.py` — pydantic-settings; без ключей приложение стартует, `/health` отдаёт `configured: false`.
- `database/connection.py` — ленивые клиенты supabase (anon + service role), `@lru_cache`.
- `app/main.py` — Jinja2 + StaticFiles, `GET /` (homepage), `GET /health`.
- `migrations/001_init.sql` — полная схема: enums, profiles, categories (+seed 9 категорий), achievements, achievement_completions, proofs, evaluations, moderation_reviews, reports; UNIQUE/FK/CHECK, индексы (в т.ч. `achievements_rank_unique` partial), triggers `set_updated_at` + `handle_new_user`.
- `requirements.txt` (fastapi, uvicorn, jinja2, pydantic-settings, supabase, httpx, python-multipart, pytest, ruff), `vercel.json` (ASGI `app.main:app`), `.env.example`, `.gitignore`, `README.md`.
- `tests/test_health.py` — 4 теста; `ruff check`/`format` — чисто; `pytest` — 4 passed; `uvicorn app.main:app` стартует, `/`, `/health`, `/static` отвечают 200.

## last_checked_commit

`eb3a23d feat(d2): RLS policies migration, applied to production Supabase`