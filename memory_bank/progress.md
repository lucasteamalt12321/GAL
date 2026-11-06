# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **100%** (D1–D12 completed).

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
| D12 | v1.1: функции, безопасность, CI   | completed |

## Changelog

### 2026-11-06 — D12 закрыт: CI зелёный, пагинация без 416, миграция 013 подтверждена

**CI.** Причина красных прогонов — незапиннутый `ruff` (в CI ставился 0.17.4 с новыми правилами). Исправлено: `ruff==0.17.4` в `requirements-dev.txt`; `datetime.UTC` вместо `timezone.utc` (UP017), убран лишний `replace("Z", "+00:00")` (FURB162). Прогон `7ed168d` — **success** (ruff check, ruff format, pytest).

**Пагинация.** PostgREST отвечал 416 на `range` за пределами выборки, глобальный обработчик превращал это в 503. Теперь список и оба лидерборда сначала запрашивают `count`, затем данные; страница за последней делает 303-редирект на валидную (`/achievements?page=2` → `?page=1`, `/leaderboard?players_page=2` → `?players_page=1`). Проверено на проде.

**Миграция 013 применена к проду** (`scripts/apply_migrations.py 013`, 201). Проверено SQL-запросами: публичная политика proofs удалена (`0`), `player_leaderboard_page` и trigger `achievements_require_creator_proof` существуют; deferred trigger в тестовой транзакции отдаёт `PUBLISH_REQUIRES_CREATOR_PROOF`; anon не видит proofs (`0`); автор может soft-delete свой pending (`1`); RPC вызывается от anon. Тестовые данные откатаны (`achievements=0`, `profiles=0`).

**Состояние.** `pytest` — **123 passed**, ruff check/format — чисто; прод отдаёт v1.1.0 (`/openapi.json`), все ключевые маршруты 200, 404 — HTML-страница. D12 completed.

### 2026-11-06 — Документация: идея и исследовательская рамка проекта

- Добавлен [`docs/idea.md`](../docs/idea.md): актуальность, принцип индивидуальных шкал, попарные сравнения, модель Bradley–Terry, цель/задачи, объект и предмет, научная новизна (с оговоркой, что новизну подхода нужно обосновать сравнением с существующими методами), практическая значимость, ожидаемый результат и краткая формулировка для жюри.
- Ссылки на документ добавлены в `README.md` и `docs/README.md`.
- Код не менялся, поведение платформы не затронуто.

### 2026-11-06 — Проверка прогресса v1.1 (код не менялся)

**Что проверено.** Память отставала от репозитория и прода.

- **Git.** `main` синхронизирован с `origin/main` (`e216724`, версия 1.1.0); рабочее дерево чистое. Коммиты v1.1 — `7e986e0` и `e216724` (05.11.2026).
- **Деплой.** Прод `gal-inky.vercel.app` отдаёт новую версию: `/` — 200 (новый лендинг), `/achievements` — 200, `/leaderboard` — 200, `/auth/update-password` — 200 (страница появилась только в v1.1), `/health` — 200 (`{"status":"ok"}`). То есть «задеплоить» из прошлого списка уже выполнено.
- **Тесты/линтеры (чистый venv).** `pytest` — **121 passed**; `ruff format --check` — 60 файлов clean; `ruff check .` — **4 ошибки** (`app/presentation.py`: 3×UP017, 1×FURB162). Причина — незапиннутый `ruff>=0.6,<1`: в CI ставится 0.17.4, где эти правила включены по умолчанию.
- **CI.** GitHub Actions: оба прогона (run 42089013447 и 42089440788) — **failure** на шаге «Ruff lint», шаги «Ruff format check» и «Tests» пропущены. CI как deliverable D12 фактически не работает.
- **Live-дефект пагинации.** При запросе страницы за пределами диапазона PostgREST отвечает 416 (`Requested Range Not Satisfiable`), `postgrest.APIError` глобальным обработчиком превращается в 503 «Сервис временно недоступен». Проверено на проде: `/achievements?page=2` → 503, `/leaderboard?page=2` → 503, при этом `/achievements?page=1` и `/leaderboard?players_page=2` → 200. На пустой БД ломается любая страница `page>1` (прод сейчас пуст: «Пока нет опубликованных достижений»), при заполненной — любая страница за последней. Причина: выборка диапазона выполняется до расчёта `pages`/редиректа, а 416 не отличается от реального сбоя Supabase.
- **Миграция 013 — не подтверждена.** В рабочей копии нет `.env` и `SUPABASE_ACCESS_TOKEN`, поэтому применить/проверить нельзя; fallback на `player_leaderboard` маскирует отсутствие `player_leaderboard_page`, отличить по HTTP невозможно.

**Вывод.** D12 остаётся `in_progress`: готовы функции, тесты и деплой; не закрыты CI (ruff), обработка 416/пагинации и подтверждение 013 на проде.

### 2026-11-05 — v1.1 (D12): полировка, недостающие функции, безопасность, CI

**Инцидент.** Прод-Supabase снова был `INACTIVE`: `/achievements` и `/leaderboard` отдавали 503 за ~0.5 с, `/` и `/auth/login` — 200. Восстановлен через Management API (`POST /restore`, 200 → `COMING_UP` → `ACTIVE_HEALTHY`); страницы снова 200.

**Аудит (3 субагента).** Найдены: заглушка «MVP в разработке» на главной, `/reports` в навигации для всех (JSON-403), отсутствие 404/403-страниц, невыводимые ошибки/`report-submitted`, сырые enum и ISO-даты, отсутствие редактирования/удаления достижений и профиля, незавершённый сброс пароля, нет истории модерации, списки молча обрезаются, публичная RLS-политика proofs, публикация без proof создателя, потеря `kwargs` при удалении cookie, `NOT_FOUND` без маппинга, 500 без ключей Supabase, нет CI, расхождения README/docs/memory bank.

**Реализовано.**
- `app/main.py`: HTML-обработчики 403/404/прочих `HTTPException`, JSON только для API-путей; `SupabaseNotConfiguredError` → 503; лендинг с топами best-effort.
- `app/presentation.py`: подписи статусов/причин/типов proof, даты (UTC+3), склонения; фильтры в `templating.py`.
- Каталог: поиск `q`, пагинация по 24, редирект за последнюю страницу, «категория не найдена», склонения.
- `achievements`: edit/soft-delete своих `pending`, серверная валидация длин, `can_edit`/`can_hide`/`eval_reason`/`report-submitted` в карточке.
- Модерация: approve сначала одобряет creator completion с proof, затем публикует; reject отклоняет заявку создателя; hide (soft-delete), отклонение pending-заявок, история решений.
- Профиль: `/settings/profile` (username/display_name/avatar), `/settings/password`; `/auth/update-password` — завершение сброса через Supabase Auth из браузера; `recover` ведёт на новую страницу.
- Лидерборды: пагинация обоих разделов по 25; `player_leaderboard_page` с fallback на старую RPC.
- UI: русская локализация, мобильное меню, таблицы со скроллом, контраст CTA, видимый фокус, skip-link, favicon, `main.js` (pre-check 4 МБ, confirm, reload).
- Безопасность/RLS: `migrations/013_ui_features_and_hardening.sql` — soft-delete policy, удаление `proofs_select_approved_public`, авторство `moderation_reviews`, deferred trigger «published ⇒ approved proof создателя», постраничный лидерборд; middleware учитывает `kwargs` cookie (max-age=0).
- Инфраструктура: `.github/workflows/ci.yml` (ruff + pytest), README/docs переписаны, `.gitignore` исправлен (`.env.example` больше не игнорируется, `.pytestlog.txt` удалён), `scripts/apply_migrations.py` принимает префикс номера.

**Проверка.** `pytest` — **121 passed** (новые `tests/test_features.py`, `conftest.py` без сети), `ruff check`/`format` — чисто. Миграция 013 проверена на проде в транзакции с `rollback` (201, объекты не созданы).

**Осталось на момент записи:** push, деплой Vercel, применение 013 к проду, live-проверка, `last_checked_commit`.

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

**Уточнение (2026-10-20).** Причина недоступности Supabase локально — **не DNS**: резолвер отдаёт реальные адреса пула (`8.47.69.0/24`, `8.6.112.0/24`; последний октет ротируется — `.0/.1/.5/.10`; AAAA нет), но TCP-коннект ко **всем** адресам диапазонов таймаутится (`Connection timed out`, curl `000`). При этом `google.com` (204) и `*.vercel.app` (`216.198.79.195`, 200) доступны. Т.е. локальная сеть блэкхолит диапазоны Supabase на уровне TCP — ни DNS, ни код приложения ни при чём. Поэтому hosts-pin и DoH проблему не решают; рабочие обходы — VPN/прокси или локальный Supabase (нужен Docker). Ошибки/зависания Supabase в приложении теперь дают 503 (см. выше), а не 500.

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

`7ed168d fix(ci,pagination): pin ruff 0.17.4, modernize datetime, avoid PostgREST 416` (2026-11-06)
