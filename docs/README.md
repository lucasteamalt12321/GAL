# GAL — Architecture Overview

## Global Achievement List v1.7

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
| `users.py`       | Публичные профили: очки, пройденные и созданные достижения |
| `settings.py`    | Настройки профиля и смена пароля                       |
| `submissions.py` | «Мои заявки»: статусы, причины, переотправка           |
| `achievements.py`| Каталог, создание, редактирование/удаление pending      |
| `completions.py` | Выполнения + доказательства                             |
| `evaluations.py` | Личная шкала и оценки                                   |
| `rankings.py`    | Achievement / Player leaderboards (пагинация)           |
| `moderation.py`  | Очередь, решения, скрытие, история                      |
| `reports.py`     | Жалобы и их рассмотрение                                |
| `lthub_auth.py`  | Вход в GAL через LTHub (OAuth client, PKCE)             |
| `oauth.py`       | GAL как OAuth 2.0 provider для LTHub (authorize/token/userinfo) |
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
| `completions.py` | Proof-файлы (лимит 4 МБ из-за Vercel), ссылки, retry, публичные списки прошедших |
| `evaluation.py`  | Personal scale, locking                                |
| `moderation.py`  | Очередь, approve/reject, hide, история                  |
| `reports.py`     | Жалобы и скрытие достижения                            |
| `profiles.py`    | Редактирование профиля, смена пароля                   |
| `bradley_terry.py`| Чистое ядро BT: сравнения, MM-оценка, rating          |
| `bt_recompute.py`| Пересчёт BT из БД через service role + RPC             |
| `ranking.py`     | Порядок рангов по BT, fallback, `player_score`         |
| `rankings.py`    | Чтение лидербордов (достижения + игроки)               |
| `oauth_provider.py` | OAuth-провайдер: клиенты, коды, токены, PKCE, userinfo |

## Аутентификация

- `POST /auth/register`, `POST /auth/login` — Supabase Auth; токены в httpOnly-cookie `gal_session` + `gal_refresh` (SameSite=Lax, `Secure` в проде). В Supabase включён `mailer_autoconfirm`: регистрация сразу возвращает сессию и логинит пользователя, без письма подтверждения (встроенный SMTP Supabase лимитирован и ненадёжен для прода).
- **Вход по никнейму или email:** поле входа принимает и ник, и email. `resolve_login_email` резолвит никнейм в email на сервере через service role (`profiles.ilike(username)` → `auth.admin.get_user_by_id`), email не утекает клиенту. Неизвестный ник даёт то же общее «Неверный email или пароль», что и неверный пароль (без раскрытия существования ника). При недоступном service role вход остаётся по email.
- **Вход через LTHub:** `GET /auth/lthub` создаёт `state` + PKCE-verifier в HttpOnly-cookie и редиректит на LTHub `/oauth/authorize`; `GET /auth/lthub/callback` проверяет state, меняет код на токен (PKCE S256), читает `/oauth/userinfo` и логинит/создаёт Supabase-пользователя через `external_identities` (миграция 015). Username берётся из логина LTHub (при занятости — суффикс/subject), display_name — из профиля LTHub. LTHub — публичный PKCE-клиент, client secret не используется. `next` (только same-origin путь) сохраняется в HttpOnly-cookie и восстанавливается после callback; `intent=register` с кнопки регистрации уходит провайдеру, чтобы LTHub после согласия создавал аккаунт, а не логинил. `POST /auth/register` также сохраняет `next` и редиректит на него после успешной регистрации.
- **GAL как OAuth-провайдер для LTHub:** `GET/POST /oauth/authorize` — consent-страница (анонимного ведёт на `/auth/login?next=...`), `POST /oauth/token` — обмен одноразового кода на opaque access token (PKCE S256, TTL кода 5 минут, токен 30 дней), `GET /oauth/userinfo` — профиль для импорта, `GET /.well-known/oauth-authorization-server` — metadata. Клиенты — статический реестр `app/services/oauth_provider.py` (без таблицы `oauth_clients`); LTHub — публичный клиент. В БД хранятся только SHA-256-хеши кодов/токенов, доступ — `service_role` (миграция 021).
- `POST /auth/logout` — `sign_out` + удаление cookie.
- `POST /auth/recover` — письмо со ссылкой на `/auth/update-password` (`APP_URL` или текущий origin).
- `GET /auth/update-password` — страница завершения сброса: токены приходят во фрагменте URL, пароль отправляется из браузера напрямую в Supabase Auth (`PUT /auth/v1/user`), anon key публичен.
- `GET /auth/me` — JSON текущего пользователя (401 без сессии).
- `POST /auth/register|login|recover` принимают form-urlencoded и JSON (`app/forms.py`); для JSON CSRF-токен допускается полем `_csrf` или заголовком `X-CSRF-Token`.
- `UserContextMiddleware` резолвит пользователя на каждый запрос; при истёкшем access-токене делает refresh и обновляет cookie; битая кука удаляется (max-age=0). Обновлённые токены кладутся в `request.state`, и `user_client_from_request` предпочитает их cookies — иначе PostgREST/Storage получили бы устаревший access-токен и ответили 401 в том же запросе.
- Профиль читается из `profiles`; роль, username, avatar доступны в `request.state.user`.

## Достижения, профили, настройки

- `GET /achievements` — публичный каталог: фильтр `category`, сортировка `rank|new`, поиск `q`, пагинация по 24.
- `GET /achievements/{id}` — карточка (автор видит свои pending); показывает причину недоступности оценки.
- `GET/POST /achievements/create` — создание (`pending`); нужен proof создателя (файл или ссылка). Создавать достижения может только пользователь, у которого уже есть хотя бы одна оценка опубликованного достижения (`has_user_evaluation`; оценки удалённых/скрытых достижений не считаются): сначала нужно пройти и оценить существующее. Правило проверяется на GET и POST, в сервисе (`create_achievement`) и в RLS (миграция 020); при блокировке форма скрывается, показывается объяснение и ссылка на каталог.
- `GET/POST /achievements/{id}/edit` — правка своего достижения, пока оно `pending`.
- `POST /achievements/{id}/delete` — soft-delete своего pending (`status=deleted`), заявки в очередь не попадают.
- `GET /users/{username}` — профиль: публично только `published`; владельцу видны свои pending/rejected; модератору — все статусы.
- `GET/POST /settings/profile` — username/display_name/avatar_url; `POST /settings/password` — смена пароля с проверкой текущего.
- Идентификатор категории для фильтра резолвится отдельным запросом по `slug`; неизвестный slug показывает «категория не найдена», а не пустой каталог.

## Доказательства и модерация

- `POST /achievements/{id}/complete` — заявка `pending` + один proof (файл ≤ 4 МБ или ссылка). Лимит 4 МБ — потолок тела serverless-функции Vercel; bucket допускает 50 МБ, но приложение принимает 4 МБ.
- Proof-файл: приватный bucket `proofs`, путь `{user_id}/{achievement_id}/{uuid}-{filename}`, строка в `proofs`. Ссылка: `proof_type=link`, URL в `storage_path`.
- Ошибки Storage переводятся в понятные тексты (`_storage_error_message`): 413 → лимит 4 МБ; 401/403/JWT/RLS → «войдите заново или приложите ссылку»; 404/bucket not found → «хранилище временно недоступно»; остальное → общий текст. Сырые ответы (`statusCode`, имена политик, бакетов) в UI не попадают, детали пишутся в лог `gal` (`logger.warning`/`exception`).
- `GET /moderation` — очередь pending достижений и заявок (модератор/админ; аноним → login, остальные → 403).
- `POST /moderation/achievements/{id}` — approve/reject. Approve сначала одобряет completion создателя (проверяя наличие proof), затем публикует достижение; reject отклоняет и заявку создателя.
- `POST /moderation/achievements/{id}/hide` — soft-delete опубликованного/ожидающего достижения, отклонение его pending-заявок, запись в историю и пересчёт рангов.
- `GET /moderation/history` — последние решения с целью, модератором и причиной.
- Просмотр файловых доказательств — через signed URL (TTL 1 час). Строки `proofs` доступны только владельцу и модератору (политика `proofs_select_approved_public` удалена в 013).
- Публикация достижения без `approved`-выполнения создателя с proof запрещена deferred constraint trigger-ом `achievements_require_creator_proof` (013).
- RLS `storage.objects`: владелец по первой части пути, модератор — через `public.is_moderator()`.

## Оценки

- **Сложность спрашивается сразу:** при создании достижения — «Ожидаемая сложность», при прохождении существующего — «Сложность». Пользователь не вводит номер, а отвечает на серию сравнений «сложнее/легче, чем …» относительно своей личной шкалы (бинарный поиск позиции вставки).
- Выбор сохраняется в `achievement_completions.requested_harder_count`; после approve модератором RPC `apply_completion_evaluation` применяет его как обычную оценку. Если у пользователя ещё нет ни одной оценки и сложность не была указана, первая approved-заявка автоматически становится якорем (позиция 1).
- **Новые оценки принимаются в любой момент**, в том числе после выхода достижения в `ranked`: каждая новая заявка/оценка пересчитывает среднее, ранги и BT-рейтинг. Оценки не блокируются (`locked` всегда false).
- `POST /achievements/{id}/evaluate` — ручная правка оценки; та же шкала и сравнения, `p_harder_count` = число своих оценённых достижений, которые сложнее нового.
- Оценка доступна только с `approved` completion; одно достижение = одна оценка (новые пользователи добавляют новые оценки).
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
- **Регистрация:** `validate_username` (3–32 симв., `[A-Za-z0-9_.-]`) + регистронезависимая проверка занятости; `validate_email` (формат `local@domain.tld`) до обращения к GoTrue; подтверждение пароля в форме. Ошибки Supabase Auth маппятся в понятные русские сообщения (`friendly_auth_error`): rate limit («Повторите через N сек»), некорректный email, «email уже зарегистрирован», неверные учётные данные, слабый пароль — вместо сырого английского текста GoTrue.
- **RLS:** служебные колонки достижений защищены guard-триггером (010); заявки/оценки/proofs видны владельцу и модератору; история модерации пишется только от имени модератора (013); вставка достижения разрешена только при наличии оценки опубликованного достижения (020).
- **Cookies:** удаление битой сессии выполняется с `max-age=0`; `Secure` в проде.
- **Ошибки:** HTML-страницы 403/404 (`errors/403.html`, `errors/404.html`) и 503 при сбое Supabase (`errors/upstream.html`); API-пути (`/auth/me`, `/health`, `/docs`) отвечают JSON.

## Устойчивость к сбоям Supabase

- PostgREST-таймаут задаётся `SUPABASE_TIMEOUT_SECONDS` (по умолчанию `6`) через `ClientOptions(postgrest_client_timeout=...)`.
- Глобальные обработчики `httpx.HTTPError`, `postgrest.exceptions.APIError` и `SupabaseNotConfiguredError` отдают `errors/upstream.html` с кодом **503** и пишут причину в лог `gal.upstream`.
- Лендинг не зависит от БД: топы подгружаются best-effort, при сбое показывается статическая часть.
- `/health` не обращается к БД и всегда отвечает JSON.

## UI

- Русскоязычный интерфейс; подписи статусов, причин жалоб, типов proof и даты — через фильтры `presentation.py`.
- Визуальный стиль — **VKUI** (как BankBot): единый `app/static/css/style.css` с токенами `--vkui-*` (палитра `@vkontakte/vkui` 8), радиусы 8/12/16 px, системный шрифт, высота шапки 56 px, компоненты кнопок, полей, карточек, чипов, бейджей, таблиц, flash и error-страниц.
- Светлая тема по умолчанию, тёмная — через `prefers-color-scheme` (`color-scheme`, два `<meta name="theme-color">`). CSS подключается с версией (`style.css?v=2`), чтобы сбросить старый кэш.
- Адаптивная шапка с мобильным меню, горизонтальный скролл таблиц, видимый фокус, skip-link, `aria`-атрибуты таблиц, `prefers-reduced-motion`.
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
- `018_auto_first_evaluation.sql` — первая approved-заявка без сохранённой сложности автоматически становится якорем (позиция 1); backfill для существующих пользователей.
- `019_live_evaluations.sql` — снятие lock, приём новых оценок после `ranked`, пересчёт рангов при каждой новой оценке.
- `020_creation_requires_evaluation.sql` — `user_has_published_evaluation(uuid)` (security definer, EXECUTE только у `authenticated`) и RLS-insert в `achievements` только при наличии оценки опубликованного достижения.
- `021_oauth_provider.sql` — GAL как OAuth 2.0 provider: `oauth_codes`, `oauth_tokens`, хранение только SHA-256-хешей, доступ исключительно `service_role`.
- `022_restore_ranking_and_proof_scheme.sql` — восстановление канонического `recompute_ranks_internal()` (BT + двухфазный bump + `security definer`), `submit_evaluation`/`apply_completion_evaluation` с порогом 2 и защитой ссылок-доказательств (`proofs_link_scheme`).
- `023_public_completions.sql` — security-definer RPC `achievement_completers(bigint)` и `user_completed_achievements(uuid)`: только approved-заявки опубликованных достижений и публичные поля профиля; гранты `anon`/`authenticated`.

**Порядок критичен: 008 → 009 → 010 → 011 → 012 → 013 → 014 → 022** (008 и 022 содержат `create or replace` функций ранжирования; после 008 обязательно переприменение 010/011/014, после 022 — итоговая версия).

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
- Минимум 2 оценки для попадания в глобальный рейтинг (иначе `?`); после этого новые оценки продолжают менять топ.
- Место определяется силой Bradley–Terry; при равной силе — средней позицией, затем зафиксированным `rank_order` и id.
- Очки игрока = `1000 / rank` каждого уникального подтверждённого достижения.
- Достижения со статусом `unknown` (`?`) дают 0 очков.

## Жизненные циклы

- Achievement: `pending → published` / `rejected`; `deleted` — автором (pending) или модератором.
- Completion: `pending → approved` / `rejected`.
- Evaluation: редактируется в любой момент (в т.ч. после `ranked`); новые пользователи добавляют новые оценки, топ пересчитывается.
- Report: `pending → accepted` / `rejected`.

## Развёртывание

- Vercel: `vercel.json`, ASGI = `app.main:app`.
- Переменные: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, опц. `SUPABASE_TIMEOUT_SECONDS`, `APP_ENV=production`, `APP_URL`.
- `SERVICE_ROLE_KEY` только на сервере.

## Качество

- `pytest` — 206 тестов: юнит-тесты ранкинга и Bradley–Terry (включая инвариант «каждая пара = одно соревнование»), роуты, CSRF, resilience, OAuth (клиент и провайдер), «Мои заявки», сложность/сравнения, загрузка proof-файлов и ошибки Storage, блокировка создания достижения без оценки, валидация email и маппинг ошибок регистрации, вход по никнейму, публичные профили и списки прошедших, hardening (XSS в ссылках, security-заголовки, HTML-ошибки валидации, транзиентные сбои auth, LTHub-аккаунты), сценарии v1.1–v1.7.
- `ruff check` / `ruff format --check`; CI в `.github/workflows/ci.yml`.
- Live-e2e: `scripts/e2e_evaluation_sql.py`, `scripts/e2e_reports_sql.py`; эксперимент BT — `scripts/bt_experiment.py`.

## Статус

Документация соответствует состоянию после v1.7: каркас, миграции 001–023, аутентификация и сброс пароля, вход через LTHub (OAuth + PKCE), GAL как OAuth-провайдер, достижения + профили + настройки, сложность при создании/прохождении, доказательства и модерация с понятными ошибками Storage и проверкой схемы ссылок, движок оценок с порогом 2, запрет создания достижения без оценки опубликованного достижения, глобальный ранкинг Bradley–Terry (каждая пара личной шкалы — отдельное соревнование), лидерборды с пагинацией, публичные профили с пройденными достижениями, карточки достижений со списком прошедших и кнопкой отправки прохождения, жалобы, «Мои заявки», CSRF/security-заголовки/HTML-ошибки, адаптивный UI в стиле VKUI (light/dark), CI. Обновлять при изменениях архитектуры, маршрутов и модулей.
