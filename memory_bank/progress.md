# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **5%** (D1 completed, остальные pending).

## Deliverables статус

| ID  | Deliverable                       | Status      |
|-----|-----------------------------------|-------------|
| D1  | Foundation + Memory Bank          | completed   |
| D2  | Database Schema + RLS             | pending     |
| D3  | Authentication                    | pending     |
| D4  | Achievements CRUD                 | pending     |
| D5  | Proofs + Moderation               | pending     |
| D6  | Evaluation Engine + Creator Eval  | pending     |
| D7  | Ranking Engine (тесты)            | pending     |
| D8  | Player Score + Leaderboard        | pending     |
| D9  | Reports                           | pending     |
| D10 | Polish (UI, security, errors)     | pending     |

## Known Issues

- Supabase-проект ещё не создан: `001_init.sql` написан, но к живой БД не применён. Живая проверка `/health` (`supabase.configured: true`) и RLS отложена до создания проекта и предоставления ключей.
- StarletteDeprecationWarning от `starlette.testclient` — warning из библиотеки, не от нашего кода.

## Changelog

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

`9882598 docs: AGENTS.md, docs/README.md, memory_bank initialization` (обновить после коммита D1).