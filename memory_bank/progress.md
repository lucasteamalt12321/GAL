# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **100%** (D1–D11 completed).

## Deliverables статус

| ID  | Deliverable                       | Status    |
|-----|-----------------------------------|-----------|
| D1  | Foundation + Memory Bank          | completed |
| D2  | Database Schema + RLS             | completed |
| D3  | Authentication                    | completed |
| D4  | Achievements CRUD                 | completed |
| D5  | Proofs + Moderation               | completed |
| D6  | Evaluation Engine + Creator Eval  | completed |
| D7  | Ranking Engine (тесты)            | completed |
| D8  | Player Score + Leaderboard        | completed |
| D9  | Reports                           | completed |
| D10 | Polish (UI, security, errors)     | completed |
| D11 | Bugfix по итогам bug-hunt 09-28   | completed |

## Changelog

### 2026-10-20 — Инцидент: `/leaderboard` отдавал 500 при сбое Supabase — устранено (resilience)

**Симптом.** На проде (`gal-inky.vercel.app`) `/leaderboard` возвращал `500 Internal Server Error`, когда запрос к Supabase зависал/обрывался.

**Причина.** Обработчики БД-страниц вызывали Supabase-сервисы без таймаута и без обработки ошибок. `postgrest` по умолчанию ждёт **120 с** (`DEFAULT_POSTGREST_CLIENT_TIMEOUT`), затем бросает `httpx.ReadTimeout`/`RemoteProtocolError`; исключение никто не ловит → FastAPI отдаёт дефолтный 500. Воспроизведено локально (TestClient): `/` → 200, `/leaderboard` → 500 (120.6 с; повтор 20.0 с). Изоляция вызовов: `leaderboard_achievements` → `ReadTimeout` (121 с), `player_leaderboard` → `RemoteProtocolError` «Server disconnected» (41 с). Триггер — нестабильный DNS/сеть до `*.supabase.co` (системный резолвер отдаёт мёртвые `8.47.69.1`/`8.6.112.1` вместо `…​.5` из DoH).

**Исправление (resilience).**
- `app/config.py` — `supabase_timeout_seconds` (env `SUPABASE_TIMEOUT_SECONDS`, дефолт `6`).
- `app/services/auth.py` — `create_client(..., options=ClientOptions(postgrest_client_timeout=...))`.
- `app/main.py` — глобальные обработчики `httpx.HTTPError` и `postgrest.exceptions.APIError` → шаблон `app/templates/errors/upstream.html` со статусом **503** + лог `gal.upstream`; покрывает все БД-страницы.
- `app/routers/rankings.py` — два независимых чтения `/leaderboard` грузятся параллельно (`ThreadPoolExecutor`), время страницы ограничено одним таймаутом.

**Проверка.** До: 500 за 120 с. После: 503 за **6.5 с** (страница «Сервис временно недоступен»). `pytest` — **98 passed** (94 + 4 новых `tests/test_resilience.py`), `ruff check`/`format` — чисто.

**Прод не сломан.** `gal-inky.vercel.app/leaderboard` — 200 (40/40), внешняя проверка 200; публичный домен один (`gal-lucasteamalt…` и `gal-git-main-…` → 302, SSO-защита Vercel). Баг проявлялся только в момент сбоя Supabase; теперь такой сбой даёт 503, а не 500.

### 2026-10-19 — D11 доставлен в прод: миграции 008–012, инцидент INACTIVE, live-e2e, автодеплой

**Конфликт миграций.** Моя `010_harden_achievements_and_ranks.sql` (коммит `842865f`) **удалена** — сломана (`LANGUAGE SQL` с PL/pgSQL-синтаксисом; guard `is_moderator()` внутри `recompute_ranks()` ломал бы оценки обычных пользователей). Приняты чужие миграции от 28.09.2026 (другой программист): `010_ranking_integrity.sql` + `011_evaluation_lock.sql` — полнее и корректнее (`recompute_ranks_internal()` без guard + guarded-обёртка, trigger `guard_ranking_fields`, data-fix locked). Упоминания `010_harden_...` в Changelog за 2026-10-18 относятся к тому коммиту и более не актуальны.

**Исправлена ложная запись memory bank:** «008/009 применены к проду» не соответствовало действительности — в прод-БД не было ни `reset_rejected_completion`, ни привилегий из 008. Фактически применены в этой сессии.

**Инцидент.** Прод-проект Supabase `wiwyitafoprxmlyndkwe` был `INACTIVE` (compute остановлен) — все страницы приложения были недоступны. Восстановлен через `POST /v1/projects/{ref}/restore` (201 → `COMING_UP` → `CHANNEL_UP`). Ошибка `42P01 relation "public.achievements" does not exist` при первой попытке применения миграций — запрос попал в базу до завершения восстановления схемы.

**Применено к проду (порядок критичен):** `008 → 009 → 010 → 011 → 012` через Management API (201). Новая `migrations/012_revoke_internal_execute.sql` — `revoke execute on recompute_ranks_internal() from anon, authenticated, service_role` (default privileges Supabase выдают execute всем ролям, revoke от PUBLIC не снимает; проверено: до 012 anon вызывал функцию, после — `42501 permission denied`). Итог проверен: функции `reset_rejected_completion`, `recompute_ranks_internal`, `guard_ranking_fields`, `submit_evaluation`, `recompute_ranks`, `player_leaderboard` — есть; 2 триггера на achievements; whitelist-гранты authenticated точные.

**Live-e2e на проде:** `scripts/e2e_evaluation_sql.py` — **16/16**, `scripts/e2e_reports_sql.py` — **5/5**; данные убраны (users/achievements/оценки/жалобы = 0, категории-seed = 8). В `e2e_evaluation_sql.py` удалён чек `rls_unlocked_self_update` (задублирован `rls_locked_update_blocked`; после 008 прямой UPDATE падает с permission denied).

**Деплой и HTTP.** Push `57e48e2`, `4eeb269` → автодеплой Vercel подтверждён: deployment `gal-mygfx8c73` (18.10 21:31:19, Ready), aliases `gal-inky.vercel.app` + `gal-git-main-...`. HTTP через DoH + `curl --resolve` (IP `216.198.79.195`): `/health`, `/`, `/achievements`, `/leaderboard`, `/auth/login`, `/docs` — **200** (маршрут логина именно `/auth/login`, `/login` → 404 — это норма).

**Контроль:** `pytest` — 94 passed, `ruff check`/`format` — чисто (до и после коммитов). `.pytestlog.txt` оставлен незакоммиченным (мусорный лог).

### 2026-10-18 — D11 Bugfix: все критические/ключевые находки закрыты

Реализованы и закоммичены исправления по баг-ханту 2026-09-28. `pytest` — **94 passed**, `ruff check`/`format` — чисто. Коммит `842865f`.

**Критические** *(миграция удалена 2026-10-19 как сломанная, см. Changelog; закрыто чужими 010/011)*:
- `migrations/010_harden_achievements_and_ranks.sql`: блокировка подделки `rank`/`ranking_status`/`average_position`/`evaluation_count` — `revoke update on achievements from authenticated; grant update` только по разрешённым колонкам (`title, description, requirements, category_id, status`).
- `migrations/010_harden_achievements_and_ranks.sql`: `recompute_ranks()` — убран публичный `execute` у `authenticated`, добавлен гард `public.is_moderator()` (defense-in-depth), `execute` оставлен у `service_role`. `submit_evaluation` продолжает вызывать SD-функцию.

**High:**
- `app/services/auth.py`: `looks_like_jwt` используется в `set_session` и `user_client_from_request`; битая кука не передаётся в supabase_auth → нет `IndexError`. `MalformedSessionTokenError` добавлен в `AUTH_ERRORS`.
- `app/templates/moderation/queue.html`: заголовок страницы исправлен на «Модерация».

**Medium (ключевые):**
- `app/routers/evaluations.py`: `_parse_position` — строгий парсинг (`isascii` + `isdigit`) + ограничение диапазона.
- `app/routers/reports.py`: добавлена проверка существования достижения в POST перед созданием жалобы.
- `app/routers/achievements.py`: создание достижения и прикрепление proof создателя обрабатываются раздельно (при ошибке proof редирект на карточку, без повторного создания).
- `migrations/010_harden_achievements_and_ranks.sql`: `submit_evaluation` при `count >= 3` всегда переводит достижение в `ranked`, при необходимости проставляет `rank_order`/`rank`, блокирует **все** оценки достижения (`locked=true`).
- `app/services/completions.py`: при наличии файла вызывается `uploaded.file.seek(0)`.
- `scripts/e2e_reports_sql.py`: при не-201 возвращается ненулевой код выхода, `_gal_e2e_results` удаляется в `finally`.
- `vercel.json`: `maxLambdaSize` → `250mb`. `requirements.txt` очищен от dev-зависимостей (pytest/ruff не тянутся в прод-бандл).

**Прочее:**
- `app/services/achievements.py`, `app/services/reports.py`, `app/services/evaluation.py`, `app/routers/reports.py`, шаблоны `achievements/create.html`, `achievements/detail.html`, `base.html` — мелкие правки, соответствующие баг-ханту (в т.ч. экранирование в поиске username, обработка edge-кейсов).
- `scripts/e2e_evaluation_sql.py`, `tests/test_reports.py` — синхронизированы с правками.

## last_checked_commit

`a74e42c docs: migrations 008-012 in README; add 012 revoke internal execute; memory bank sync (prod delivery, INACTIVE incident, migration conflict)` (2026-10-19)
