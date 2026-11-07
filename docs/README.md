# GAL — Architecture Overview

## Global Achievement List v1.1

Платформа для глобального рейтинга человеческих достижений из разных сфер через единую относительную шкалу сложности, построенную на личных сравнениях пользователей.

> Идея и исследовательская рамка проекта (Bradley–Terry, попарные сравнения,
> задачи и новизна) — [idea.md](idea.md).

## Схема

```text
Browser (Jinja2/JS/CSS)
        │ HTTPS
        ▼
     Vercel
        │
     FastAPI
        │
   ┌─────┴─────┬─────────┐
   ▼           ▼         ▼
PostgreSQL  Auth     Storage
(Supabase)  (Supabase) (Supabase)
```

- **FastAPI** — бизнес-логика (ранкинг, scoring, модерация, оценки), SSR-шаблоны.
- **Supabase** — PostgreSQL, авторизация, приватное хранение доказательств.

## Модули

### `app/routers/`

| Модуль           | Назначение                                             |
|------------------|--------------------------------------------------------|
| `auth.py`        | Вход, регистрация, выход, recover/update-password      |
| `users.py`       | Публичные профили                                      |
| `settings.py`    | Настройки профиля и смена пароля                       |
| `submissions.py` | «Мои заявки»: статусы, причины, переотправка           |
| `achievements.py`| Каталог, создание, редактирование/удаление pending      |
| `completions.py` | Выполнения + доказательства                             |
| `evaluations.py` | Личная шкала и оценки                                   |
| `rankings.py`    | Achievement / Player leaderboards (пагинация)           |
| `moderation.py`  | Очередь, решения, скрытие, история                      |
| `reports.py`     | Жалобы и их рассмотрение                                |
| `health.py`      | `/health`                                               |

### `app/` (инфраструктура)

| Модуль             | Назначение                                                    |
|--------------------|---------------------------------------------------------------|
| `main.py`          | Сборка FastAPI, роутеры, error-handlers, лендинг               |
| `config.py`        | Настройки (pydantic-settings), `is_production`, таймауты       |
| `presentation.py`  | Подписи enum-значений, склонения, форматирование дат           |
| `templating.py`    | Общий `Jinja2Templates` + регистрация фильтров                 |
| `middleware.py`    | `UserContextMiddleware` — пользователь, CSRF-cookie, сессия    |
| `dependencies.py`  | `get_current_user` / `get_current_moderator` / `get_current_admin` |
| `csrf.py`          | Double-submit CSRF (app-level dependency)                      |
| `forms.py`         | Чтение POST-полей из form/multipart/JSON                       |
| `database/`        | Ленивые клиенты Supabase (anon + service role)                 |

### `app/services/`

| Модуль           | Назначение                                             |
|------------------|--------------------------------------------------------|
| `auth.py`        | Cookie-сессия Supabase Auth, resolve/refresh, валидация |
| `achievements.py`| Каталог, CRUD собственных pending, профили             |
| `completions.py` | Proof-файлы (лимит 4 МБ из-за Vercel), ссылки, retry    |
| `evaluation.py`  | Personal scale, locking                                |
| `moderation.py`  | Очередь, approve/reject, hide, история                  |
| `reports.py`     | Жалобы и скрытие достижения                            |
| `profiles.py`    | Редактирование профиля, смена пароля                   |
| `bradley_terry.py`| Чистое ядро BT: сравнения, MM-оценка, rating          |
| `bt_recompute.py`| Пересчёт BT из БД через service role + RPC             |
| `ranking.py`     | Порядок рангов по BT, fallback, `player_score`         |
| `rankings.py`    | Чтение лидербордов (достижения + игроки)               |

## Аутентификация

- `POST /auth/register`, `POST /auth/login` — Supabase Auth; токены в httpOnly-cookie `gal_session` + `gal_refresh` (SameSite=Lax, `Secure` в проде).
- **Вход через LTHub:** `GET /auth/lthub` создаёт `state` + PKCE-verifier в HttpOnly-cookie и редиректит на LTHub `/oauth/authorize`; `GET /auth/lthub/callback` проверяет state, меняет код на токен (PKCE S256), читает `/oauth/userinfo` и логинит/создаёт Supabase-пользователя через `external_identities` (миграция 015). Username берётся из логина LTHub (при занятости — суффикс/subject), display_name — из профиля LTHub. LTHub — публичный PKCE-клиент, client secret не используется.
- `POST /auth/logout` — `sign_out` + удаление cookie.
- `POST /auth/recover` — письмо со ссылкой на `/auth/update-password` (`APP_URL` или текущий origin).
- `GET /auth/update-password` — страница завершения сброса: токены приходят во фрагменте URL, пароль отправляется из браузера напрямую в Supabase Auth (`PUT /auth/v1/user`), anon key публичен.
- `GET /auth/me` — JSON текущего пользователя (401 без сессии).
- `POST /auth/register|login|recover` принимают form-urlencoded и JSON (`app/forms.py`); для JSON CSRF-токен допускается полем `_csrf` или заголовком `X-CSRF-Token`.
- `UserContextMiddleware` резолвит пользователя на каждый запрос; при истёкшем access-токене делает refresh и обновляет cookie; битая кука удаляется (max-age=0).
- Профиль читается из `profiles`; роль, username, avatar доступны в `request.state.user`.

## Достижения, профили, настройки

- `GET /achievements` — публичный каталог: фильтр `category`, сортировка `rank|new`, поиск `q`, пагинация по 24.
- `GET /achievements/{id}` — карточка (автор видит свои pending); показывает причину недоступности оценки.
- `GET/POST /achievements/create` — создание (`pending`); нужен proof создателя (файл или ссылка).
- `GET/POST /achievements/{id}/edit` — правка своего достижения, пока оно `pending`.
- `POST /achievements/{id}/delete` — soft-delete своего pending (`status=deleted`), заявки в очередь не попадают.
- `GET /users/{username}` — профиль: публично только `published`; владельцу видны свои pending/rejected; модератору — все статусы.
- `GET/POST /settings/profile` — username/display_name/avatar_url; `POST /settings/password` — смена пароля с проверкой текущего.
- Идентификатор категории для фильтра резолвится отдельным запросом по `slug`; неизвестный slug показывает «категория не найдена», а не пустой каталог.

## Доказательства и модерация

- `POST /achievements/{id}/complete` — заявка `pending` + один proof (файл ≤ 4 МБ или ссылка). Лимит 4 МБ — потолок тела serverless-функции Vercel; bucket допускает 50 МБ, но приложение принимает 4 МБ.
- Proof-файл: приватный bucket `proofs`, путь `{user_id}/{achievement_id}/{uuid}-{filename}`, строка в `proofs`. Ссылка: `proof_type=link`, URL в `storage_path`.
- `GET /moderation` — очередь pending достижений и заявок (модератор/админ; аноним → login, остальные → 403).
- `POST /moderation/achievements/{id}` — approve/reject. Approve сначала одобряет completion создателя (проверяя наличие proof), затем публикует достижение; reject отклоняет и заявку создателя.
- `POST /moderation/achievements/{id}/hide` — soft-delete опубликованного/ожидающего достижения, отклонение его pending-заявок, запись в историю и пересчёт рангов.
- `GET /moderation/history` — последние решения с целью, модератором и причиной.
- Просмотр файловых доказательств — через signed URL (TTL 1 час). Строки `proofs` доступны только владельцу и модератору (политика `proofs_select_approved_public` удалена в 013).
- Публикация достижения без `approved`-выполнения создателя с proof запрещена deferred constraint trigger-ом `achievements_require_creator_proof` (013).
- RLS `storage.objects`: владелец по первой части пути, модератор — через `public.is_moderator()`.

## Оценки

- **Сложность спрашивается сразу:** при создании достижения — «Ожидаемая сложность», при прохождении существующего — «Сложность». Пользователь не вводит номер, а отвечает на серию сравнений «сложнее/легче, чем …» относительно своей личной шкалы (бинарный поиск позиции вставки).
- Выбор сохраняется в `achievement_completions.requested_harder_count`; после approve модератором RPC `apply_completion_evaluation` применяет его как обычную оценку (сдвиг позиций, переход `unknown → ranked` при 3+, lock, пересчёт рангов).
- `POST /achievements/{id}/evaluate` — ручная правка оценки; та же шкала и сравнения, `p_harder_count` = число своих оценённых достижений, которые сложнее нового.
- Оценка доступна только с `approved` completion; одно достижение = одна оценка.
- При 3+ оценках достижение переходит `unknown → ranked`: оценки `locked`, фиксируется `rank_order`, `recompute_ranks()` перенумеровывает все ranked.
- Ошибки RPC (`AUTH_REQUIRED` / `NOT_APPROVED` / `NOT_FOUND` / `LOCKED`) маппятся в `EvaluationError` и показываются flash-сообщением.
- Атомарная логика — security definer функции `public.submit_evaluation` и `public.apply_completion_evaluation`.

## Ранкинг и лидерборды

Глобальная сложность считается моделью **Bradley–Terry** по попарным сравнениям из личных шкал (подробно — [`bradley_terry.md`](bradley_terry.md)):

- `app/services/bradley_terry.py` — чистые функции: `build_comparisons` (все пары внутри шкалы, вес `1/(k-1)`), `fit_bradley_terry` (регуляризованный MM, prior κ=0.5), `win_probability`.
- `app/services/bt_recompute.py` — service role читает все `evaluations` и опубликованные достижения, считает силы, вызывает RPC `apply_bt_ratings(jsonb)`.
- RPC пишет `bt_strength` (логит), `bt_rating = 1000 + 400·s/ln10`, `bt_comparisons`, `bt_updated_at` и пересчитывает `rank` (сортировка `bt_strength desc, average_position asc, rank_order asc, id asc`).
- Пересчёт запускается после оценки, модерации и принятия жалобы; ошибка только логируется и не ломает сценарий. Ручной запуск — `python scripts/recompute_bt.py`.
- `app/services/ranking.py` — чистое ядро порядка (BT desc, fallback на среднюю позицию), `player_score(rank) = 1000/rank`.
- `GET /leaderboard` — две секции с независимой пагинацией по 25: топ достижений (rank, BT-рейтинг, сравнения, оценки, категория) и топ игроков.
- Очки игрока = `1000 / rank` за каждое `approved` выполнение ранжированного опубликованного достижения.
- `public.player_leaderboard_page(p_limit, p_offset)` — постраничный security definer-агрегат (013).
- Две секции грузятся параллельно (`ThreadPoolExecutor`), поэтому время страницы ограничено одним таймаутом Supabase.

## Жалобы

- `GET/POST /achievements/{id}/report` — причины `duplicate/incorrect/spam/offensive/other`; одна pending-жалоба на достижение от пользователя.
- `GET /reports` — очередь модератора; `POST /reports/{id}/accept|reject`.
- Принятие скрывает достижение (`status=deleted`), отклоняет его pending-заявки и пересчитывает ранги; ошибка пересчёта логируется.

## Безопасность

- **CSRF:** double-submit cookie `gal_csrf`; проверка в app-level зависимости `csrf_protect` (`app/main.py`), а не в middleware — чтение тела не ломает multipart-загрузку. Enforcement только при `APP_ENV=production`. Токен доступен шаблонам через `request.state.csrf_token`.
- **Регистрация:** `validate_username` (3–32 симв., `[A-Za-z0-9_.-]`) + регистронезависимая проверка занятости; подтверждение пароля в форме.
- **RLS:** служебные колонки достижений защищены guard-триггером (010); заявки/оценки/proofs видны владельцу и модератору; история модерации пишется только от имени модератора (013).
- **Cookies:** удаление битой сессии выполняется с `max-age=0`; `Secure` в проде.
- **Ошибки:** HTML-страницы 403/404 (`errors/403.html`, `errors/404.html`) и 503 при сбое Supabase (`errors/upstream.html`); API-пути (`/auth/me`, `/health`, `/docs`) отвечают JSON.

## Устойчивость к сбоям Supabase

- PostgREST-таймаут задаётся `SUPABASE_TIMEOUT_SECONDS` (по умолчанию `6`) через `ClientOptions(postgrest_client_timeout=...)`.
- Глобальные обработчики `httpx.HTTPError`, `postgrest.exceptions.APIError` и `SupabaseNotConfiguredError` отдают `errors/upstream.html` с кодом **503** и пишут причину в лог `gal.upstream`.
- Лендинг не зависит от БД: топы подгружаются best-effort, при сбое показывается статическая часть.
- `/health` не обращается к БД и всегда отвечает JSON.

## UI

- Русскоязычный интерфейс; подписи статусов, причин жалоб, типов proof и даты — через фильтры `presentation.py`.
- Тёмная тема, адаптивная шапка с мобильным меню, горизонтальный скролл таблиц, видимый фокус, skip-link, `aria`-атрибуты таблиц.
- `static/js/main.js` — мобильное меню, pre-check размера файла (4 МБ), подтверждение необратимых действий, кнопка обновления на 503.

## Миграции

- `001_init.sql` — схема, enum-типы, seed категорий, триггеры.
- `002_rls.sql` — RLS-политики, grants, helper-функции.
- `003_fix_handle_new_user.sql` — `handle_new_user` как `security definer`.
- `004_storage_proofs.sql` — bucket `proofs` (private, 50 МБ) + политики `storage.objects`.
- `005_evaluation_rpc.sql` — движок оценок: `rank_order`, `recompute_ranks()`, `submit_evaluation()`.
- `006_fix_evaluation_rpc.sql` — фикс `42702 ambiguous`.
- `007_player_leaderboard.sql` — `public.player_leaderboard()`.
- `008_hardening.sql` — revoke лишних RPC, двухфазный пересчёт рангов (`rank + 1 000 000 000`), запрет прямого REST-обновления служебных колонок.
- `009_retry_completion_rpc.sql` — `reset_rejected_completion`, `remove_empty_pending_completion`.
- `010_ranking_integrity.sql` — column-level grants, `recompute_ranks_internal()`, guarded `recompute_ranks()`, trigger `guard_ranking_fields`, data-fix.
- `011_evaluation_lock.sql` — lock-режим оценок.
- `012_revoke_internal_execute.sql` — revoke execute на `recompute_ranks_internal()`.
- `013_ui_features_and_hardening.sql` — soft-delete своих pending, удаление публичной политики proofs, авторство истории модерации, deferred trigger proof создателя, `player_leaderboard_page`.
- `014_bradley_terry.sql` — колонки `bt_strength/bt_rating/bt_comparisons/bt_updated_at`, guard новых полей, ранжирование по BT, RPC `apply_bt_ratings(jsonb)` для `service_role`.
- `015_external_identities.sql` — привязка внешних identity (LTHub) к профилям; доступ только `service_role`.
- `016_completion_review_visibility.sql` — автор заявки видит `moderation_reviews` по своей заявке (причина отклонения).
- `017_completion_difficulty.sql` — `requested_harder_count`, RPC `apply_completion_evaluation` (сложность из заявки → оценка после approve).

**Порядок критичен: 008 → 009 → 010 → 011 → 012 → 013 → 014** (008 содержит `create or replace` устаревших функций, после неё обязательно переприменение 010/011).

## Данные

```text
profiles
categories
achievements
achievement_completions
proofs
evaluations
moderation_reviews
reports
```

**Производные значения** (не канон): `achievements.rank`, `bt_strength`, `bt_rating`, `bt_comparisons`, `average_position`, суммы очков игроков — пересчитываются из первичных данных (`evaluations`).

## Главная цепочка

```text
Achievement → Creator Proof → Moderation → Published
   → User Completion → Completion Proof → Moderation
   → Personal Evaluation → Pairwise Comparisons
   → Bradley–Terry Strength → Global Rank
   → 1000 / Rank → Player Leaderboard
```

## Ключевые правила

- Достижение публикуется только после подтверждения собственного выполнения создателем (закреплено триггером 013).
- Оценка доступна только обладателю `approved` completion; одно достижение = одна оценка.
- Минимум 3 оценки для попадания в глобальный рейтинг (иначе `?`).
- После появления числового рейтинга оценки фиксируются (`locked`).
- Место определяется силой Bradley–Terry; при равной силе — средней позицией, затем зафиксированным `rank_order` и id.
- Очки игрока = `1000 / rank` каждого уникального подтверждённого достижения.
- Достижения со статусом `unknown` (`?`) дают 0 очков.

## Жизненные циклы

- Achievement: `pending → published` / `rejected`; `deleted` — автором (pending) или модератором.
- Completion: `pending → approved` / `rejected`.
- Evaluation: editable пока достижение `unknown`; `locked` после `ranked`.
- Report: `pending → accepted` / `rejected`.

## Развёртывание

- Vercel: `vercel.json`, ASGI = `app.main:app`.
- Переменные: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, опц. `SUPABASE_TIMEOUT_SECONDS`, `APP_ENV=production`, `APP_URL`.
- `SERVICE_ROLE_KEY` только на сервере.

## Качество

- `pytest` — 152 теста: юнит-тесты ранкинга и Bradley–Terry, роуты, CSRF, resilience, OAuth, «Мои заявки», сложность/сравнения, сценарии v1.1–v1.4.
- `ruff check` / `ruff format --check`; CI в `.github/workflows/ci.yml`.
- Live-e2e: `scripts/e2e_evaluation_sql.py`, `scripts/e2e_reports_sql.py`; эксперимент BT — `scripts/bt_experiment.py`.

## Статус

Документация соответствует состоянию после v1.4: каркас, миграции 001–017, аутентификация и сброс пароля, вход через LTHub (OAuth + PKCE), достижения + профили + настройки, сложность при создании/прохождении, доказательства и модерация, движок оценок, глобальный ранкинг Bradley–Terry, лидерборды с пагинацией, жалобы, «Мои заявки», CSRF/ошибки/адаптивный UI, CI. Обновлять при изменениях архитектуры, маршрутов и модулей.
