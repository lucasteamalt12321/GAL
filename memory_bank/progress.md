# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **95%** (D1–D9 completed; D10 pending).

## Deliverables статус

| ID  | Deliverable                       | Status      |
|-----|-----------------------------------|-------------|
| D1  | Foundation + Memory Bank          | completed   |
| D2  | Database Schema + RLS             | completed   |
| D3  | Authentication                    | completed   |
| D4  | Achievements CRUD                 | completed   |
| D5  | Proofs + Moderation               | completed   |
| D6  | Evaluation Engine + Creator Eval  | completed   |
| D7  | Ranking Engine (тесты)            | completed   |
| D8  | Player Score + Leaderboard        | completed   |
| D9  | Reports                           | completed   |
| D10 | Polish (UI, security, errors)     | pending     |

## Known Issues

- StarletteDeprecationWarning от `starlette.testclient` — warning из библиотеки, не от нашего кода.
- PAT `SUPABASE_ACCESS_TOKEN` хранится в локальном `.env` (не коммитится); в `.env.example` — пустой плейсхолдер.
- **`SUPABASE_JWT_SECRET` в `.env` пуст** — подпись собственных JWT для локальных live-прогонов недоступна; получение секрета через Management API не предусмотрено (не отдаётся). Поэтому live-проверки выполняются через SQL-канал Management API (см. `scripts/e2e_evaluation_sql.py`). Auth-эндпоинты `auth/v1/token` и `admin/users` локально нестабильны (см. ниже).
- **Локальная сеть к `supabase.co` нестабильна**: `auth/v1/token` изредка висит до таймаута (ReadTimeout), `auth/v1/admin/users` периодически отдаёт транзиентные 500. Надёжный канал — Management API `api.supabase.com` (works стабильно). Для live-проверок используется он: создание auth-пользователей и очистка — прямым SQL, проверка RPC — через `set local request.jwt.claims` + `set local role authenticated` в одном транзакционном пакете.
- Storage RLS live-проверка завершена ранее (см. Changelog): upload под user-JWT, signed URL (относительный от API, storage3 джойнит в absolute), fetch по подписи 200, anon заблокирован (bucket невидим).
- Осиротевшие e2e-данные от прерванных live-прогонов удалены (achievements `E2E Evaluation Target` id 7–10 + пользователи `@gal-e2e.test`, включая всех 10 пробных).
- Уникальность `username`: только БД-индекс `profiles_username_unique(lower(username))`; при коллизии регистрация падает на `handle_new_user` (обработать в D10).
- `/auth/recover` вызывается без `redirect_to` — письма ведут на дефолтный URL Supabase (настроить в D10).
- Supabase отклоняет зарезервированные email-домены (`example.com`, `test.com`) и лимитирует письма (email rate limit) — важно для тестов регистрации.
- Email-конфирмация включена: зарегистрированный пользователь входит только после подтверждения письма.
- **D6/007**: при локальных live-прогонах через SQL-канал остается транзакционный пакет (атомарен: при ошибке ничего не персистится); вспомогательная таблица `public._gal_e2e_results` дропается. Проверку в UI через настоящий JWT (HTTP-путь) отложил из-за нестабильной сети — покрыта unit-тестами и SQL live-проверкой.

## Changelog

### 2026-09-25 — D9 Reports completed
- `app/services/reports.py` — `create_report` (authenticated, проверка дубля своей жалобы, `REPORTS_ERRORS`), `list_pending_reports` (embed achievement+reporter через FK-алиасы `!reports_achievement_id_fkey`/`!reports_reporter_id_fkey`), `decide_report` (модератор: update `status/resolved_at/resolved_by/resolution_reason`; accept → `achievements.status='deleted'` — публичный список фильтрует published).
- `app/routers/reports.py` — `GET/POST /achievements/{id}/report` (anon → login), `GET /reports` (очередь модератора, anon → login, user → 403), `POST /reports/{id}/accept|reject`; подключён в `app/main.py`.
- Шаблоны `reports/report.html` (форма жалобы, причины: duplicate/incorrect/spam/offensive/other) и `reports/queue.html` (карточки с решением accept/reject); ссылка «Пожаловаться» на странице достижения; `.btn-danger` уже был в CSS.
- `tests/test_reports.py` — 13 тестов (аноним/403/404, рендер формы и очереди, redirect-флоу ошибок, вызовы сервиса accept/reject). **Итого 69 passed; ruff чист.**
- **Live-проверка `scripts/e2e_reports_sql.py` — 5/5 чеков:** reporter вставляет жалобу под своей ролью (RLS), anon жалобы не видит (rows=0) и не может их менять (0 строк), модератор видит очередь, accept скрывает достижение (`status=deleted`). В скрипт добавлены ретраи на сетевые ошибки (`_post_query`), т.к. локальная сеть к API нестабильна (SSL UNEXPECTED_EOF). Обучающие моменты из live: `profiles.role` — enum `user_role` (нужен `::public.user_role`); роль меняет только `service_role` по `request.jwt.claims` (триггер `prevent_profile_role_change`), поэтому `set local request.jwt.claims = '{"role":"service_role"}'` ставится в начале транзакции.

### 2026-09-25 — D8 Player Score + Leaderboard completed
- `migrations/007_player_leaderboard.sql` — security definer функция `public.player_leaderboard()`: агрегат по approved completions ранжированных опубликованных достижений (`score = 1000 / rank`, round 2, unknown → 0) + `achievement_count`; `group by profiles`, `order by score desc, username asc`. Применена к проду (2 фикса колонки: `p.id as user_id`, GROUP BY по `p.id`).
- Причина функции в БД: RLS закрывает `achievement_completions` (только свои/модератор), лидерборд публичный, поэтому агрегированный доступ — через `security definer` (owner postgres) + `grant execute to anon, authenticated`.
- `app/services/rankings.py` — `leaderboard_achievements` (ranked+published, `.order("rank")`, REST anon), `player_leaderboard` (rpc).
- `app/routers/rankings.py` — `GET /leaderboard`: achievement-ранкинг (rank, avg position, count, category) + player-ранкинг (позиция, score, count); `_enrich_players` нормализует score в float и добавляет позицию.
- `app/templates/rankings/leaderboard.html` — две секции-таблицы + пустые состояния; nav `Players` → `Leaderboard` (`/leaderboard`); CSS `.leaderboard-table/.lb-*`.
- `app/main.py` — подключён `rankings.router`.
- `tests/test_rankings.py` — 3 теста (рендер строк, пустое состояние, порядок из сервиса). **Итого 56 passed; ruff чист.**
- **Live-проверка (SQL-канал, расширен `e2e_evaluation_sql.py` до 17 чеков):** `player_score_1000` (3 игрока × score 1000 при rank=1), `achievement_leaderboard_rank1` (достижение в лидерборде), `player_leaderboard_anon` (функция читается под ролью anon — grant работает). Порядок в SQL — `order by score desc, username asc` (unit-тест проверяет проброс порядка из сервиса).

### 2026-09-25 — D7 Ranking Engine completed
- `app/services/ranking.py` — чистый ранк-движок: `RankCandidate` (dataclass), `eligible_for_ranking` (фильтр как в SQL-WHERE `recompute_ranks`: published + ranked + числовое `average_position`), `compute_ranks` (порядок `average_position asc, rank_order asc, id asc` → `{id: rank}`, ранг 1 = самая низкая средняя), `player_score` (`1000/rank`, round 2; rank=None/≤0 → 0.0 — Score policy MVP).
- `tests/test_ranking.py` — 14 тестов: порядок рангов, ничьи по `rank_order` и по `id`, пустой/одиночный список, фильтры eligible, `player_score` (1→1000, 2→500, 3→333.33, None/0/отрицательный → 0). **Итого 53 passed; ruff чист.**
- Решение по транзакционности ранкинга (§57): пересчёт происходит атомарно внутри `public.submit_evaluation()` (та же транзакция: переход + временный rank + lock + `recompute_ranks()`), отдельный фоновый пересчёт MVP не требуется.
- Live: переход в `ranked` с `rank=1` и компактность рангов уже подтверждены SQL live-проверкой D6 (`c_third_ranked`, `ranks_compact`).

### 2026-09-25 — D6 Evaluation Engine completed + критический баг RPC найден live
- `migrations/005_evaluation_rpc.sql` — `rank_order` (tie-breaker), `recompute_ranks()` (security definer, компактная перенумерация по `average_position asc, rank_order asc, id asc` среди `ranked+published`), `submit_evaluation(p_achievement_id, p_harder_count)` (security definer): валидация `auth.uid()`, наличие `approved` completion (`NOT_APPROVED`), unlocked (`LOCKED`), двухпроходный сдвиг позиций в личной шкале (сжать старую позицию / освободить новую), upsert, пересчёт `average_position`/`evaluation_count`, переход `unknown→ranked` при 3+ оценках с временным `rank=max+1` + `rank_order=random()` + lock всех оценок, финальный `recompute_ranks()`. Применена к проду.
- **Критический баг, найденный live-проверкой:** `42702 ambiguous` в `submit_evaluation` — `RETURNS TABLE (achievement_id …)` создаёт OUT-переменные, конфликтующие с колонками в `UPDATE`/`ON CONFLICT`. Оценки в проде не работали. Исправлено миграцией `006_fix_evaluation_rpc.sql`: директива `#variable_conflict use_column` + алиасы. Применена, live-проверка подтвердила работу.
- `app/services/evaluation.py` — `EvaluationError` (+ `AuthRequiredError`/`NotApprovedError`/`LockedError` по тексту ошибок RPC), `get_user_evaluation`, `list_user_scale` (join achievements/categories), `can_evaluate`, `submit`, `evaluate_context`.
- `app/routers/evaluations.py` — `GET`/`POST /achievements/{id}/evaluate`: аноним → login, 404 неизвестное достижение, блок оценки на карточке, `?error=` / `?info=evaluated`.
- Шаблоны `evaluations/evaluate.html` (выбор позиции 1..N+1, «Первая оценка — якорь №1» для пустой шкалы), блок оценки в `achievements/detail.html`, CSS каунтер/селлекта.
- `tests/test_evaluations.py` (10 тестов: аноним/login-флоу, ошибки сервиса, редиректы, `?info=evaluated`). **Итого 39 passed; ruff чист.**
- `scripts/apply_migrations.py` — применение миграций через Management API; `scripts/e2e_evaluation_sql.py` — **live-проверка D6 на проде через единый SQL-канал** (создание auth-пользователей прямым SQL, тест `submit_evaluation` + RLS через `set local role authenticated` + `request.jwt.claims` в одной транзакции, автозачистка). **14/14 проверок прошли**: якорь первой оценки=1, upsert без дублей, агрегаты 1/2/3, переход в ranked (count=3, avg=1, rank=1), все оценки locked, LOCKED-отказ при повторной оценке, NOT_APPROVED без completion, RLS: update собственной unlocked строки ok, update locked — 0 строк, insert без approval — заблокирован.
- Побочное: `scripts/e2e_evaluation.py` (HTTP-путь live-проверки) переведён на самоподписанные JWT (`mint_jwt`), но требует `SUPABASE_JWT_SECRET`; ожидает либо секрет в `.env`, либо стабильной сети — аукс.
- Очистка остатков: достижения `E2E Evaluation Target` (7–10) и все пользователи `@gal-e2e.test` (10 шт.) удалены.

### 2026-09-24 — D5 live-проверка завершена (Storage + прод)
- Storage RLS подтверждён live через прямой REST: upload файла под user-JWT в `proofs/{uid}/1/...` → 200; `create_signed_url` возвращает относительный путь `/object/sign/...`, storage3 при использовании джойнит его с base URL в absolute (баг в приложении отсутствует); fetch по signed URL → 200 с корректным содержимым; анонимный прямой fetch и list → 400/404 (bucket невидим), список недоступен.
- Очищены 4 осиротевших `gal-e2e-*` профиля от прерванных прогонов (в т.ч. каскад: moderation_reviews → proofs → completions → achievements; достижений в БД не осталось — `achievements` пуст).
- Прод проверен: `/health` 200, `/` 200, `/achievements` 200, `/achievements/create` anon → 303.

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

`cb10bbb feat(d9): reports (submit, moderator queue, hide achievement on accept)`