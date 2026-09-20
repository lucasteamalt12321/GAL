# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **15%** (D1, D2 completed; D3–D10 pending).

## Deliverables статус

| ID  | Deliverable                       | Status      |
|-----|-----------------------------------|-------------|
| D1  | Foundation + Memory Bank          | completed   |
| D2  | Database Schema + RLS             | completed   |
| D3  | Authentication                    | pending     |
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

## Changelog

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

`6633afe docs(memory_bank): sync last_checked_commit after D1`