# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **50%** (D1–D5 completed; D6–D10 pending).

## Deliverables статус

| ID  | Deliverable                       | Status      |
|-----|-----------------------------------|-------------|
| D1  | Foundation + Memory Bank          | completed   |
| D2  | Database Schema + RLS             | completed   |
| D3  | Authentication                    | completed   |
| D4  | Achievements CRUD                 | completed   |
| D5  | Proofs + Moderation               | completed   |
| D6  | Evaluation Engine + Creator Eval  | pending     |
| D7  | Ranking Engine (тесты)            | pending     |
| D8  | Player Score + Leaderboard        | pending     |
| D9  | Reports                           | pending     |
| D10 | Polish (UI, security, errors)     | pending     |

## Known Issues

- StarletteDeprecationWarning от `starlette.testclient` — warning из библиотеки, не от нашего кода.
- PAT `SUPABASE_ACCESS_TOKEN` хранится в локальном `.env` (не коммитится); в `.env.example` — пустой плейсхолдер.
- Storage bucket `proofs` создан и настроен (D5). Bucket `avatars` не создавался (в MVP аватары не используются).
- Live-проверка файловой части D5 (загрузка файла proof через приложение + signed URL) откладывалась из-за нестабильной сети (ReadTimeout/SSL к Supabase) — unit-тесты маршрутов зелёные, декор proofs через signed URL протестирован на уровне API; перепроверить при восстановлении сети.
- Возможно остались осиротевшие e2e-данные (username `gal-e2e-*`) от прерванных live-прогонов — очистить при восстановлении сети.
- Уникальность `username`: только БД-индекс `profiles_username_unique(lower(username))`; при коллизии регистрация падает на `handle_new_user` (обработать в D10).
- `/auth/recover` вызывается без `redirect_to` — письма ведут на дефолтный URL Supabase (настроить в D10).
- Supabase отклоняет зарезервированные email-домены (`example.com`, `test.com`) и лимитирует письма (email rate limit) — важно для тестов регистрации.
- Email-конфирмация включена: зарегистрированный пользователь входит только после подтверждения письма.

## Changelog

### 2026-09-21 — D5 Proofs + Moderation completed
- `migrations/004_storage_proofs.sql` — bucket `proofs` (private, `file_size_limit` 50 МБ) + политики `storage.objects`: insert/delete владельца по `(storage.foldername(name))[1] = auth.uid()::text`, select владельца и модератора (`public.is_moderator()`). Применена к проду через Management API; bucket + 4 политики подтверждены запросом.
- `app/services/completions.py` — `submit_completion`, `create_completion` (unique-ошибка 23505 → `AlreadyCompletedError`), `attach_proof` (файл ≤50МБ → upload в `{uid}/{achievement_id}/{uuid}-{file}` и строка в `proofs`, при сбое — best-effort `storage.remove`; либо link-proof), `proof_view`/`decorate_proofs` (signed URL TTL 3600, не падает при сетевых ошибках), `PROOFS_BUCKET`, `COMPLETION_ERRORS`.
- `app/services/moderation.py` — `list_pending_achievements`/`list_pending_completions` (с embedded `category`/`creator`/`user`/`proofs` и `proof_views`), `decide_achievement`/`decide_completion` (update по `status=pending`, запись в `moderation_reviews`, `approved_at` на approve), авто-approve completion создателя (`_approve_creator_completion`) при approve достижения.
- `app/routers/completions.py` — `POST /achievements/{id}/complete` (аноним → login; не-published → 404; ошибки → redirect `?error=`).
- `app/routers/moderation.py` — `GET /moderation` + `POST /moderation/achievements/{id}` и `POST /moderation/completions/{id}`; аноним → login, не-модератор → 403.
- Create-флоу достижения требует proof создателя (файл или ссылка), после создания вызывается `submit_completion` (проверки + ошибки). `achievements/detail.html` — форма подачи доказательства, статус своей заявки, список proofs; `moderation/queue.html`; ссылка «Moderation» в nav для модераторов/админов.
- Фикс: `File(None)` в defaults → `Annotated[UploadFile | None, File()]` (ruff B008); `upsert` на Storage-upload — булевым (`"false"` трактуется storage3 как truthy).
- `tests/test_moderation.py` — 11 тестов (аноним/403, очередь, решения, авто-вход через monkeypatch `resolve_user`). Итого `pytest` — 29 passed; `ruff check`/`format` — чисто.
- Live E2E (частично, сеть нестабильна): create с link-proof → pending (+ completion создателя + proof), очередь показывает, submit на pending → 404, approve → published + creator completion auto-approved + `moderation_reviews` записана; user submit на published → 303. Файловая часть проверки прервана из-за сети (см. Known Issues).

### 2026-09-21 — D4 Achievements CRUD completed (+ критический фикс регистрации)
- **Критический баг найден и исправлен:** `handle_new_user` был `security invoker`, из-за чего вставка в `public.profiles` блокировалась RLS и регистрация падала с `Database error creating new user`. Миграция `003_fix_handle_new_user.sql` помечает функцию `security definer` (owner `postgres`); применена к проду.
- **Баг совместимости исправлен:** `maybe_single().execute()` в новом postgrest возвращает `None` при отсутствии строки — все вызовы (`_fetch_profile`, `get_achievement`, `get_profile`, `_category_id_by_slug`) проверяют `None`.
- `app/services/achievements.py` — `list_categories`, `list_achievements` (фильтр по `slug`, сортировка `rank`/`new`), `get_achievement`, `list_achievements_by_creator`, `get_profile`, `create_achievement`; эмбеддинги `category` и `creator` в одном запросе.
- `app/services/auth.py` — добавлены `user_client_from_request` и `client_for_request` (публичное чтение anon, запись/свой pending — user JWT); `user_client_from_request` устойчив к невалидной сессии.
- `app/routers/achievements.py` — `GET /achievements` (фильтры), `GET /achievements/create` (нужен вход), `POST /achievements/create`, `GET /achievements/{id}`; `app/routers/users.py` — `GET /users/{username}`.
- Шаблоны `achievements/list.html`, `achievements/detail.html`, `achievements/create.html`, `users/profile.html`; CSS (карточки, чипы, бейджи статусов, метрики).
- `tests/test_achievements.py` — 8 тестов (моки сервисного слоя); итого `pytest` — 18 passed, `ruff check` — чисто.
- Интеграционная проверка (с самоочисткой данных): регистрация + автосоздание профиля, insert достижения под RLS user-JWT, владелец видит свой `pending`, свежий anon — нет (RLS подтверждён), E2E create через веб-роут (`303 → карточка`), anon на pending → 404, публичный список пуст.

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

`3849479 feat(d5): proofs (storage bucket) and moderation queue/decisions`