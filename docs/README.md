# GAL — Architecture Overview

## Global Achievement List v1.18 (D29)

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
- **Vercel** — основной деплой и канонический адрес GAL; не удалялся.
- **Appwrite Function `gal-mirror`** — зеркало GAL в self-hosted Appwrite: вызывает тот же FastAPI-объект, но **не заменяет** Supabase.
- **Supabase** — PostgreSQL, авторизация, приватное хранение доказательств; остаётся единственной БД.

## Модули

### `app/routers/`

| Модуль           | Назначение                                             |
|------------------|--------------------------------------------------------|
| `auth.py`        | Вход, регистрация, выход, recover/update-password      |
| `users.py`       | Публичные профили: очки, пройденные и созданные достижения |
| `settings.py`    | Настройки профиля и смена пароля                       |
| `submissions.py` | «Мои заявки»: выполнения и заявки топ-5, статусы, переотправка |
| `achievements.py`| Каталог, создание, редактирование/удаление pending      |
| `completions.py` | Выполнения + доказательства                             |
| `evaluations.py` | Личная шкала и оценки                                   |
| `rankings.py`    | Игроки: поиск + пагинация; чтение achievement leaderboard для главной |
| `top5.py`        | Топ-5: редирект списка в «Мои заявки», отзыв, карточка заявки |
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
| `completions.py` | Proof-файлы (несколько файлов, суммарно до 16 МБ), ссылки, комментарий к заявке, retry, публичные списки прошедших; после 036 — атомарная отправка через service-role RPC `submit_completion_with_proofs`, SHA-256 файлов и сверка номера попытки |
| `submissions.py` | Общая точка обработки ошибок заявок: понятные сообщения и распознавание `DUPLICATE_SUBMISSION`, `DUPLICATE_PROOF`, `ALREADY_COMPLETED`, ошибок proof, отсутствия миграции 036 и service-ключа |
| `evaluation.py`  | Personal scale, locking                                |
| `moderation.py`  | Очередь, approve/reject, hide, история                  |
| `reports.py`     | Жалобы и скрытие достижения                            |
| `profiles.py`    | Редактирование профиля, смена пароля                   |
| `bradley_terry.py`| Чистое ядро BT: сравнения, MM-оценка, rating, win_probability |
| `bt_recompute.py`| Пересчёт BT и временных сил топ-5 из БД через service role + RPC |
| `top5.py`        | Заявки топ-5 при прохождении, закрытие решением модератора |
| `ranking.py`     | Порядок рангов по BT, fallback, `player_score`         |
| `rankings.py`    | Лидерборд игроков с поиском и fallback до миграции 040; чтение achievement leaderboard |
| `oauth_provider.py` | OAuth-провайдер: клиенты, коды, токены, PKCE, userinfo |

## Аутентификация

- `POST /auth/register`, `POST /auth/login` — Supabase Auth; токены в httpOnly-cookie `gal_session` + `gal_refresh` (SameSite=Lax, `Secure` в проде). В Supabase включён `mailer_autoconfirm`: регистрация сразу возвращает сессию и логинит пользователя, без письма подтверждения (встроенный SMTP Supabase лимитирован и ненадёжен для прода).
- **Вход по никнейму или email:** поле входа принимает и ник, и email. `resolve_login_account` резолвит никнейм на сервере через service role (`profiles.ilike(username)`), email не утекает клиенту. Для LTHub-аккаунта email строится из `external_identities.subject` (`lthub-oauth-<subject>@example.com`), поэтому поиск ника не зависит от Supabase Admin API; для обычного профиля используется `auth.admin.get_user_by_id`. Неизвестный ник даёт то же общее «Неверный email или пароль», что и неверный пароль. Сбой PostgREST/Admin API выбрасывает `LoginLookupUnavailableError`, и форма показывает «Сервис входа временно недоступен», а не обвиняет пользователя в неверном пароле. Вход по прямому email не требует поиска профиля.
- **Вход через LTHub:** `GET /auth/lthub` создаёт `state` + PKCE-verifier в HttpOnly-cookie и редиректит на LTHub `/oauth/authorize`; `GET /auth/lthub/callback` проверяет state, меняет код на токен (PKCE S256), берёт профиль LTHub из ответа токена (поле `user`; `/oauth/userinfo` — только запасной путь) и логинит/создаёт Supabase-пользователя через `external_identities` (миграция 015). Перед редиректом на LTHub в фоновом потоке прогреваются её OAuth-эндпоинты (GET без тела, grant-запрос не создаётся): их serverless-контейнеры иначе стартуют холодными (обмен токена занимал ~11 c), и колбэк не влезает в таймаут внешнего прокси. Тайминги хопов пишутся в лог при `LTHUB_TIMING_LOG=1`. Username берётся из логина LTHub (при занятости — суффикс/subject), display_name — из профиля LTHub. LTHub — публичный PKCE-клиент, client secret не используется. `next` (только same-origin путь) сохраняется в HttpOnly-cookie и восстанавливается после callback; `intent=register` с кнопки регистрации уходит провайдеру, чтобы LTHub после согласия создавал аккаунт, а не логинил. `POST /auth/register` также сохраняет `next` и редиректит на него после успешной регистрации.
- **Пароль LTHub-аккаунта в GAL (D22):** для аккаунта, зарегистрированного через LTHub, пароль — это пароль в LTHub. `POST /auth/login` сначала проверяет локальную копию пароля; если она не подошла, GAL сверяет пароль с API LTHub (`POST /api/auth/login`, `login` + `password`, ответ `user_id` сверяется с subject identity) и, если LTHub подтвердил, делает этот пароль паролем аккаунта в GAL (`auth.admin.update_user_by_id`) — после этого тот же пароль работает и в LTHub, и в GAL. Отказ LTHub («неверный логин или пароль») и недоступность LTHub различаются: при недоступности показывается отдельное сообщение, локальная копия пароля продолжает работать. LTHub-fallback запускается только после явного отказа credentials от Supabase; классификатор использует тип `AuthInvalidCredentialsError` либо `code=invalid_credentials` со статусом 400/401, поэтому 5xx с похожим текстом не считаются неверным паролем. При 5xx/сетевом сбое Supabase показывается «Сервис входа временно недоступен», без попытки скрыть сбой проверкой LTHub. Lookup identity в login-пути строгий: сбой PostgREST отличается от отсутствия identity; HTTP 429 LTHub получает отдельное сообщение; неудачное зеркалирование пароля не превращается в отказ credentials. Логин LTHub хранится в `external_identities.lthub_login` (миграция 033) и обновляется при каждом OAuth-входе. Пока пользовательский пароль не подтверждён (`password_synced = false`), служебный пароль можно перевыпустить; после подтверждения OAuth-вход его не трогает — иначе пароль LTHub перестал бы работать после каждого входа через LTHub. Смена пароля в `/settings/password` помечает пароль подтверждённым (для LTHub-аккаунта в UI есть пояснение, что пароль в LTHub не меняется). Настройки: `LTHUB_API_BASE_URL`, `LTHUB_LOGIN_URL`, `LTHUB_PASSWORD_CHECK_ENABLED`, `LTHUB_REQUEST_TIMEOUT_SECONDS`.
- **GAL как OAuth-провайдер для LTHub:** `GET/POST /oauth/authorize` — consent-страница (анонимного ведёт на `/auth/login?next=...`), `POST /oauth/token` — обмен одноразового кода на opaque access token (PKCE S256, TTL кода 5 минут, токен 30 дней), `GET /oauth/userinfo` — профиль для импорта, `GET /.well-known/oauth-authorization-server` — metadata. Клиенты — статический реестр `app/services/oauth_provider.py` (без таблицы `oauth_clients`); LTHub — публичный клиент. В БД хранятся только SHA-256-хеши кодов/токенов, доступ — `service_role` (миграция 021).
- `POST /auth/logout` — `sign_out` + удаление cookie.
- `POST /auth/recover` — письмо со ссылкой на `/auth/update-password` (`APP_URL` или текущий origin).
- `GET /auth/update-password` — страница завершения сброса: токены приходят во фрагменте URL, пароль отправляется из браузера напрямую в Supabase Auth (`PUT /auth/v1/user`), anon key публичен.
- `GET /auth/me` — JSON текущего пользователя (401 без сессии).
- `POST /auth/register|login|recover` принимают form-urlencoded и JSON (`app/forms.py`); для JSON CSRF-токен допускается полем `_csrf` или заголовком `X-CSRF-Token`.
- `UserContextMiddleware` резолвит пользователя на каждый запрос; при истёкшем access-токене делает refresh и обновляет cookie; битая кука удаляется (max-age=0). Обновлённые токены кладутся в `request.state`, и `user_client_from_request` предпочитает их cookies — иначе PostgREST/Storage получили бы устаревший access-токен и ответили 401 в том же запросе.
- Профиль читается из `profiles`; роль, username, avatar доступны в `request.state.user`.

## Достижения, профили, настройки

- `GET /achievements` — публичный каталог: фильтр `category`, сортировка `rank|new` (по умолчанию `rank` — по сложности), поиск `q`, фильтр `rating=all|rated` (по умолчанию `all`), пагинация по 24. `rated` применяет `rank IS NOT NULL` до подсчёта и пагинации: карточки с `?` исчезают, а временные позиции `#N*` с непустым `rank` остаются. Поиск и ссылки фильтров сохраняют `category`, `sort`, `rating` и сбрасывают страницу.
- `GET /achievements/{id}` — карточка (автор видит свои pending); показывает место, BT-силу и прошедших пользователей с их доказательствами; причина недоступности оценки выводится отдельно.
- `GET/POST /achievements/create` — создание (`pending`); нужен proof создателя (файл или ссылка). Создавать достижения может только пользователь, у которого уже есть хотя бы одна оценка опубликованного достижения (`has_user_evaluation`; оценки удалённых/скрытых достижений не считаются): сначала нужно пройти и оценить существующее. Правило проверяется на GET и POST, в сервисе (`create_achievement`) и в RLS (миграция 020); при блокировке форма скрывается, показывается объяснение и ссылка на каталог.
- `GET/POST /achievements/{id}/edit` — правка своего достижения, пока оно `pending`.
- `POST /achievements/{id}/delete` — soft-delete своего pending (`status=deleted`), заявки в очередь не попадают.
- `GET /users/{username}` — профиль: публично только `published`, пройденные достижения показываются с доказательствами; владельцу видны свои pending/rejected; модератору — все статусы.
- `GET/POST /settings/profile` — username/display_name/avatar_url; `POST /settings/password` — смена пароля с проверкой текущего.
- Идентификатор категории для фильтра резолвится отдельным запросом по `slug`; неизвестный slug показывает «категория не найдена», а не пустой каталог.

## Доказательства и модерация

- **Правило приёма заявки (D28, миграция 039):** достаточно хотя бы одного из трёх — файл(ы), ссылка или комментарий с непробельным текстом; комбинации разрешены, полностью пустая заявка отклоняется (`_check_submission_content` в сервисе + `PROOF_REQUIRED` в RPC). RPC проверяет только первые 2000 символов комментария, которые действительно сохраняются. Исключение — автор своего достижения: ему обязателен файл или ссылка (`require_proof=is_creator` в `/complete` и `require_proof=True` в форме создания), потому что публикация без approved-прохождения автора с доказательством запрещена триггером 013 и комментарий его не заменяет.
- `POST /achievements/{id}/complete` — заявка `pending` с файлами, ссылкой и/или комментарием: **несколько файлов** (суммарно до 16 МБ, не более 10); каждый файл — отдельная строка `proofs`. Лимит 16 МБ — продуктовое правило (`MAX_ATTACHMENT_BYTES`, настраивается env); фактический потолок деплоя ограничен платформой (Vercel обрезает тело serverless-функции ~4.5 МБ — там нужен `MAX_ATTACHMENT_BYTES=4194304`), bucket допускает 50 МБ.
- **Комментарий к прохождению** — один на заявку («в рамках этого документа», а не отдельного файла) и с миграцией 039 полноценное содержание заявки (D28): хранится в `achievement_completions.comment` (миграция 035), пишется вместе с INSERT заявки либо через security definer RPC `set_completion_comment` (владелец, заявка `pending`; под RLS пользователь не может обновлять `achievement_completions`). Виден владельцу и модераторам всегда, остальным — по правилу `proofs_visible` (публичные RPC `achievement_completers`/`user_completed_achievements` пересозданы с колонкой `comment`).
- Частично загруженные вложения откатываются: RPC `remove_completion_proofs` (владелец + `pending`) удаляет доказательства заявки, fallback — service role; объекты Storage снимаются, заявка остаётся `pending` и отправляется заново.
- Proof-файл: приватный bucket `proofs`, путь `{user_id}/{achievement_id}/{uuid}-{filename}`, строка в `proofs`. Ссылка: `proof_type=link`, URL в `storage_path`.
- Ошибки Storage переводятся в понятные тексты (`_storage_error_message`): 413 → лимит файла; 401/403/JWT/RLS → «войдите заново или приложите ссылку»; 404/bucket not found → «хранилище временно недоступно»; остальное → общий текст. Сырые ответы (`statusCode`, имена политик, бакетов) в UI не попадают, детали пишутся в лог `gal` (`logger.warning`/`exception`).
- `GET /moderation` — очередь pending достижений и заявок (модератор/админ; аноним → login, остальные → 403).
- `POST /moderation/achievements/{id}` — approve/reject. Approve сначала одобряет completion создателя (проверяя наличие proof), затем публикует достижение; reject отклоняет и заявку создателя.
- `POST /moderation/achievements/{id}/hide` — soft-delete опубликованного/ожидающего достижения, отклонение его pending-заявок, запись в историю и пересчёт рангов.
- `GET /moderation/history` — последние решения с целью, модератором и причиной.
- `GET /submissions` — «Мои заявки»: все заявки пользователя в одной вкладке — о выполнении (статус, причина отклонения, переотправка, ссылки на собственные доказательства: ссылка — внешний URL, файл — signed URL) и в топ-5 (запрошенное место, статус, решение; при отсутствии схемы 027 секция мягко пустеет).
- Просмотр файловых доказательств — через signed URL (TTL 1 час). Строки `proofs` читают только владелец и модератор; для публичных списков прошедших (approved + published) доказательства отдаёт security definer RPC `achievement_completers`/`user_completed_achievements` (026), а ссылки на файлы подписывает service role на сервере.
- **Видимость доказательства** задаётся при отправке прохождения (`achievement_completions.proofs_visible`, миграция 028, применена 2026-11-15). По умолчанию `true`: доказательство видно всем, пока автор сам не скрыл его. Если автор скрыл доказательство, публичные RPC возвращают пустой список proofs, поэтому ссылка не выводится рядом с прохождением. Владелец и модераторы видят скрытые доказательства (проверка `auth.uid()`/`is_moderator()` в RPC; очередь модерации читает proofs напрямую под RLS). Верификация создателя скрыта быть не может: триггер принудительно ставит `proofs_visible = true`.
- Публикация достижения без `approved`-выполнения создателя с proof запрещена deferred constraint trigger-ом `achievements_require_creator_proof` (013).
- RLS `storage.objects`: владелец по первой части пути, модератор — через `public.is_moderator()`.
- После 036 отправка прохождения перестаёт быть последовательностью «INSERT заявки → INSERT proofs»: файлы загружаются заранее, байтовый SHA-256 считается на сервере, полный набор proof уходит одним service-role RPC. Прямой INSERT в `proofs` у `authenticated` отозван, поэтому подделать отпечаток или добавить дубль через PostgREST нельзя. Legacy-путь `remove_completion_proofs` (035) сохраняется только для БД без 036.

## Отпечатки заявок и защита от идентичных отправок (v1.15 / D25)

- **Идентичность определяется в БД.** Отпечаток — SHA-256 канонического JSON `{algorithm_version: 1, submission_type, submitter_id, payload}`; клиент не передаёт готовый хеш. Текст нормализуется (NFC, trimming, нижний регистр, схлопывание пробелов), URL — нижний регистр схемы/хоста, удаление портов `:80`/`:443`, путь `/` по умолчанию. Байтовый SHA-256 файлов вычисляет backend до загрузки, отпечаток ссылки — PostgreSQL.
- **Типы заявок разделены:** `achievement`, `completion`, `top5`, `report`. Ключ реестра — `(submission_type, submitter_id, fingerprint)`. Прохождение и заявка в топ-5, отправленные одной формой, дают разные записи и не конфликтуют; payload топ-5 включает proofs прохождения, но ключ — `top5`.
- **Payload по типам:** achievement — нормализованные title/description/requirements + категория; completion — достижение, `requested_harder_count`, `proofs_visible`, комментарий и отсортированный набор proof-хешей; top5 — достижение, позиция, обоснование, `evidence_url`/`evidence_note`, proofs прохождения; report — достижение, причина, описание.
- **Реестр `public.submission_fingerprints`:** `id`, `submission_type`, `submitter_id`, `fingerprint`, `submission_id`, `first_attempt_no`, `last_attempt_no`, `canonical_payload`, даты; RLS включён; уникальный ключ — окончательный арбитр, `pg_advisory_xact_lock` сериализует одновременные вставки; дубликат поднимает `23505 DUPLICATE_SUBMISSION`. Отпечаток сохраняется после отклонения, отмены и удаления proof — история попыток не стирается.
- **Триггеры:** `achievements_submission_fingerprint` (INSERT и UPDATE полей title/description/requirements/category_id; повторное сохранение того же pending-текста разрешено), `zz_top5_applications_fingerprint` (BEFORE INSERT, префикс `zz` гарантирует запуск после `top5_applications_guard`), `reports_submission_fingerprint`. Для прохождения регистрация идёт внутри RPC, отдельного триггера нет.
- **Proof-отпечатки:** `proofs.content_hash` — SHA-256 байтов файла или SHA-256 канонической ссылки; CHECK 64 hex-символа; частичный UNIQUE `(completion_id, content_hash)` запрещает одинаковое доказательство внутри одной заявки. Триггер `proofs_content_hash` создаётся после backfill, чтобы не сломать исторические дубли; невалидные legacy-URL получают `legacy-path:*` и пересчитываются скриптом.
- **Атомарная отправка:** `public.submit_completion_with_proofs(p_user_id, p_achievement_id, p_harder_count, p_proofs_visible, p_comment, p_proofs)` — `security definer`, только `service_role` (иначе `SYSTEM_ONLY`). В одной транзакции: блокировка `(user, achievement)`, проверка доступности достижения, переиспользование `rejected`-заявки, счётчик `submission_attempt`, замена proofs, регистрация отпечатка. При дубликате откат возвращает прежние proofs.
- **Обратная совместимость:** старые строки получают `submission_attempt >= 1`, реестр заполняется по одной записи на группу идентичных (`distinct on`, `on conflict do nothing`), исторические дубли не удаляются. После применения 036 запускается `scripts/backfill_proof_hashes.py`, затем `refresh_legacy_submission_fingerprints()` (только service role) — файловые proof получают фактические SHA-256.
- **Права:** `revoke all on submission_fingerprints from anon, authenticated`; `grant select, insert, update … to service_role`; `revoke insert on achievement_completions`/`proofs` от `anon, authenticated`; execute хелперов канонизации и регистрации отозван у клиентских ролей. Отзываются все сигнатуры `reset_rejected_completion`, `set_completion_comment`, `remove_completion_proofs`, чтобы старые RPC не обходили новую атомарную отправку.
- **Деградация:** до применения 036 работает legacy-путь; если реестр уже есть, а RPC ещё не в кэше PostgREST (`PGRST202`), показывается «Обновление базы данных ещё применяется»; без service-ключа — «Отправка прохождения временно недоступна».

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

- `app/services/bradley_terry.py` — чистые функции: `build_comparisons` (все пары внутри шкалы, вес `1/(k-1)`), `fit_bradley_terry` (регуляризованный MM, prior κ=0.5), `with_bootstrap_intervals` (кластерный бутстрэп по личным шкалам), `win_probability`.
- `app/services/bt_recompute.py` — service role читает все `evaluations` и опубликованные достижения, считает силы и интервалы, вызывает RPC `apply_bt_ratings(jsonb)`.
- RPC пишет `bt_strength` (логит), `bt_rating = 1000 + 400·s/ln10`, `bt_comparisons`, `bt_rating_se`/`bt_rating_ci_low`/`bt_rating_ci_high`, `bt_updated_at` и пересчитывает `rank` (сортировка `bt_strength desc, average_position asc, rank_order asc, id asc`).
- Лидерборд устойчив к деплою до миграции 025: при ошибке о неизвестных колонках интервала он повторяет запрос без них (`LEGACY_RANKED_SELECT`); после применения 025 (2026-11-14) читает SE/CI, значения заполняются пересчётом BT.
- Пересчёт запускается после оценки, модерации и принятия жалобы; ошибка только логируется и не ломает сценарий. Ручной запуск — `python scripts/recompute_bt.py`.
- `app/services/ranking.py` — чистое ядро порядка (BT desc, fallback на среднюю позицию) и `player_score(bt_rating, max_bt_rating) = round(1000 · 10^((bt_rating - max_bt_rating) / 400), 2)`.
- `GET /leaderboard` — вкладка **«Игроки»** (прежнее название «Рейтинг» заменено, URL сохранён для совместимости). Только таблица игроков с очками, число достижений, поиск `q` и пагинация по 25. Поиск нечувствителен к регистру, обрезает пробелы и ищет буквальную подстроку в `username` или `display_name`; `%` и `_` не являются wildcard-символами. Порядок — `score DESC, username ASC`. Вкладка достижений с этой страницы убрана; achievement leaderboard остаётся отдельным сервисом для главной страницы.
- `public.player_leaderboard_search_page(p_limit, p_offset, p_query)` — security-definer RPC поиска и пагинации игроков (D29, миграция 040; с 041 использует общий BT-агрегат): агрегирует очки в БД, применяет literal `strpos(lower(...))`-поиск, возвращает `total_count` и ограничивает размер страницы 1–100. До применения 040 сервис ловит только `PGRST202`, использует legacy `player_leaderboard()` и выполняет фильтрацию, сортировку и пагинацию в Python; прочие ошибки Supabase наружу не скрываются. Для пустой страницы за пределами выборки сервис отдельно запрашивает `total_count` с `offset=0`, чтобы редирект вёл на последнюю существующую страницу.
- Страница игроков делает один RPC-запрос. Очки D30 считаются относительно глобального максимума BT-рейтинга: максимум даёт 1000, разница в 400 рейтингов уменьшает награду в 10 раз. Участвуют только approved-прохождения опубликованных ranked-достижений с постоянным местом и `bt_rating`; чисто временные позиции и `unknown` дают 0. Очки профиля используют ту же формулу и глобальный максимум.

## Топ-5 и временный рейтинг

Постоянный рейтинг и алгоритм BT не меняются: заявка в топ-5 лишь добавляет временную силу поверх постоянного топа (миграции 027, 031).

Заявка в топ-5 и заявка на прохождение — разные заявки даже при отправке из одной формы: в реестре 036 они имеют типы `top5` и `completion` и разные ключи. Топ-5-заявка ссылается на `completion_id`, но её отпечаток считается по своим полям (место, обоснование, ссылка, proofs прохождения); триггер с префиксом `zz` срабатывает после guard 031/034. Общая пользовательская ошибка дубликата приходит из `app/services/submissions.py`.

- Заявка подаётся **вместе с прохождением**: в форме доказательства (`POST /achievements/{id}/complete`) любой пользователь отмечает чекбокс «Заявка в топ-5» и указывает запрашиваемое место 1–5 и обоснование сложности. Доказательство — proof самого прохождения; заявка привязана к нему через `top5_applications.completion_id` (031). Отдельного маршрута подачи нет (`/top5/apply` удалён).
- **Автор достижения подаёт заявку при создании** (`POST /achievements/create`, миграция 034): та же форма заявки вынесена в общий партиал `partials/top5_claim_fields.html`; заявка создаётся вместе с прохождением верификации создателя и привязывается к нему. Guard и RLS-политика вставки допускают такую заявку только для автора и только с его собственным прохождением; для чужих достижений правила прежние (`published` + автор ИЛИ владелец прохождения). Место в топ-5 выставляет модератор после публикации; до публикации кнопка «принять как сложное» не показывается и сервис отклоняет такое решение. Отклонение или скрытие достижения закрывает его активные заявки.
- Чекбокс доступен, пока достижение опубликовано, имеет меньше 3 обычных оценок, не занимает постоянное место в топ-5 и по нему нет активной заявки. Одна активная заявка на достижение (partial unique index).
- Заявки с топ-5 приходят в **общую очередь модерации** (`GET /moderation`): бейдж «Топ-5: просит #N», обоснование, селект места модератора 1–6 (по умолчанию — запрошенное) и три кнопки: `approved_top5` («Принять как сложное»), `approved` («Принять» — заявка отклоняется), `rejected` («Отказать»). Прохождение и заявка закрываются одним решением (`moderation.decide_completion`); старая отдельная секция очереди осталась только для legacy-заявок без `completion_id`.
- `GET /top5` — редирект в «Мои заявки» (`/submissions`), отдельной вкладки в навигации нет; `GET /top5/{id}` — карточка заявки с журналом событий; автор может отозвать заявку (`POST /top5/{id}/cancel`, редирект в `/submissions`). `POST /moderation/top5/{id}` — прямое решение по заявке (legacy-сценарий): `approved` с ручным местом 1–6, `rejected` с причиной, `cancelled`.
- Независимая экспертиза временно отключена: приглашения и отдельные оценки экспертов недоступны, а legacy-оценки с `evaluator_type='expert'` не участвуют в пересчёте.
- Одобренная заявка даёт **временную** позицию: достижение показывается в общем топе с `*` (`#3*`), может занимать слот топ-5 и сдвигать других, но постоянные места существующих достижений не становятся временными. `display_rank` собирается вьюхой `public.public_achievements`; `permanent_rank` сохраняется отдельно.
- Временная сила считается `app/services/bt_recompute.py::recompute_temporary_ratings` как **середина между соседями** выбранного места в рабочем списке (постоянные ranked-достижения плюс ранее одобренные временные, вставленные в порядке решений): есть оба соседа — среднее их `bt_strength`; только нижний — его сила плюс зазор до следующего (минимум `TOP5_MIN_GAP`), либо `TOP5_EDGE_GAP` на краю; только верхний — зеркально; список пуст — стартовая сила кандидата. Место модератора — среднее его голосов `top5_evaluations`, иначе запрошенное автором.
- Переход: `evaluation_count < 3` — временная позиция; на третьей независимой обычной оценке RPC снимает временную запись, пересчитывает постоянный BT и место (выше или ниже), `*` исчезает.
- Безопасность: модератор не может выставить место собственной заявке; обычные оценки и решения модераторов хранятся раздельно; все решения журналируются (`moderation_reviews`, `top5_events`). Скрытие достижения закрывает его активные заявки.
- Совместимость: при отсутствии схемы 027 (`PGRST205`) очередь и страницы топ-5 аккуратно пустеют; до применения 031 колонка `completion_id` отсутствует (`PGRST204`) — заявки не привязываются к прохождениям, очередь работает по-старому, чекбокс не создаёт заявок.

## Жалобы

- `GET/POST /achievements/{id}/report` — причины `duplicate/incorrect/spam/offensive/other`; одна pending-жалоба на достижение от пользователя.
- `GET /reports` — очередь модератора; `POST /reports/{id}/accept|reject`.
- Принятие скрывает достижение (`status=deleted`), отклоняет его pending-заявки и пересчитывает ранги; ошибка пересчёта логируется.

## Безопасность

- **CSRF:** double-submit cookie `gal_csrf`; проверка в app-level зависимости `csrf_protect` (`app/main.py`), а не в middleware — чтение тела не ломает multipart-загрузку. Enforcement только при `APP_ENV=production`. Токен доступен шаблонам через `request.state.csrf_token`.
- **Регистрация:** `validate_username` (3–32 симв., `[A-Za-z0-9_.-]`) + регистронезависимая проверка занятости; `validate_email` (формат `local@domain.tld`) до обращения к GoTrue; подтверждение пароля в форме. Ошибки Supabase Auth маппятся в понятные русские сообщения (`friendly_auth_error`): rate limit («Повторите через N сек»), некорректный email, «email уже зарегистрирован», неверные учётные данные, слабый пароль — вместо сырого английского текста GoTrue.
- **RLS:** служебные колонки достижений защищены guard-триггером (010); заявки/оценки/proofs видны владельцу и модератору; история модерации пишется только от имени модератора (013); вставка достижения разрешена только при наличии оценки опубликованного достижения (020).
- **D25/036:** прямой INSERT в `achievement_completions` и `proofs` у `authenticated` отозван; реестр отпечатков доступен только `service_role`. Отправка прохождения вызывает `security definer` RPC с проверкой `auth.role() = 'service_role'`, поэтому защита не обходится прямым PostgREST-запросом. Все helper-функции канонизации и регистрации скрыты от клиентских ролей.
- **Cookies:** удаление битой сессии выполняется с `max-age=0`; `Secure` в проде.
- **Ошибки:** HTML-страницы 403/404 (`errors/403.html`, `errors/404.html`) и 503 при сбое Supabase (`errors/upstream.html`); API-пути (`/auth/me`, `/health`, `/docs`) отвечают JSON.

## Устойчивость к сбоям Supabase

- PostgREST-таймаут задаётся `SUPABASE_TIMEOUT_SECONDS` (по умолчанию `6`) через `ClientOptions(postgrest_client_timeout=...)`.
- Глобальные обработчики `httpx.HTTPError`, `postgrest.exceptions.APIError` и `SupabaseNotConfiguredError` отдают `errors/upstream.html` с кодом **503** и пишут причину в лог `gal.upstream`.
- Лендинг не зависит от БД: топы подгружаются best-effort, при сбое показывается статическая часть.
- `/health` не обращается к БД и всегда отвечает JSON.

## Appwrite-зеркало (D20)

- Self-hosted Appwrite: endpoint `https://aw2.ravva.su/v1`, проект `6b019993003c1f1df715` («Global achievements list»). Проект принадлежит классному руководителю; доступ — по API-ключу из `.env.local` (`APPWRITE_API_KEY`). **Миграции БД не выполнялись**: Vercel остаётся основным деплоем, Supabase — единственной БД.
- Функция `gal-mirror`: Python 3.12, `version v5`, entrypoint `main.py`, `execute: any`, timeout 60 с, build/runtime `s-8vcpu-8gb`; активный деплой `6b086ced6382b07b215a`.
- Адаптер `appwrite/functions/gal-mirror/main.py` принимает `context.req` (method/path/query/headers/body), прогоняет запрос через `httpx.ASGITransport` с тем же `app.main:app`, что и Vercel, и возвращает `context.res.binary`. Повторяющиеся `Set-Cookie` сохраняются списком заголовков; служебные `x-appwrite-*`/`x-open-runtimes-*` и hop-by-hop поля отбрасываются. До импорта приложения адаптер принудительно задаёт `SUPABASE_TIMEOUT_SECONDS=2.0`, чтобы ответ успеть передать через прокси aw2.
- open-runtimes импортирует точку входа как `function.main`, поэтому адаптер добавляет свой каталог в `sys.path` до `from app.main import app`; без этого `ModuleNotFoundError: app` давал 503 на каждом запросе.
- Сборка: `python scripts/build_appwrite_mirror.py` копирует текущий `app/` (шаблоны и статика включены), адаптер и отдельный `requirements.txt` (без `uvicorn`) в `appwrite/build/gal-mirror/` (каталог в `.gitignore`). Деплой: `appwrite functions create-deployment --function-id gal-mirror --code appwrite/build/gal-mirror --entrypoint main.py --commands "pip install -r requirements.txt" --activate`. Перед прод-деплоем сборка идёт из изолированного снимка зафиксированного HEAD, чтобы исключить параллельные незакоммиченные правки.
- Переменные функции: `SUPABASE_*` (секреты), `SUPABASE_TIMEOUT_SECONDS=2.0`, `APP_ENV=production`, `APP_URL`/`MIRROR_BASE_URL` = `https://gal.ravva.su`. Ключи Appwrite в функцию **не** передаются.
- Вызов: `appwrite functions create-execution --function-id gal-mirror --method GET --path /health` (поддерживаются `--path` с query, `--headers`, `--body`). Публичный домен `gal.ravva.su` подключён proxy rule `6b02d026bc09176f991b` к функции; DNS `CNAME sites.aw2.ravva.su`, статус правила `verified`. В Appwrite 2.7 домены подключаются не через `/v1/functions/{id}/domains` (404), а через **proxy rules**.
- Проверено 2026-11-26 после hotfix: `/health` — 200; `/` — 200 за 2.5–3.9 с вместо `ERR_EMPTY_RESPONSE`; `/achievements` при недоступном Supabase отдаёт страницу 503 за 2.5 с; страница входа открывается в браузере без console errors.
- **Таймаут ингресса.** Фронтовой nginx aw2 обрывает ответы длиннее ~5 с (`net::ERR_EMPTY_RESPONSE` в браузере, при этом сама функция отработала успешно). 2026-11-26 главная получала `200` только через 6.1–6.3 с из-за Supabase-таймаута 6 с; адаптер зеркала теперь ограничивает PostgREST до 2 с. Медленные OAuth-хопы LTHub также могут оборваться: после оптимизации (`46480a0`: профиль из ответа токена + прогрев LTHub) колбэк обычно укладывается в ~2.7 с, но для надёжности нужен `proxy_read_timeout`/`proxy_send_timeout` 60 с для `gal.ravva.su` на aw2 (доступ к серверу у проекта нет). Телеметрия: переменная функции `LTHUB_TIMING_LOG=1` пишет тайминги хопов в логи execution.

## UI

- Русскоязычный интерфейс; подписи статусов, причин жалоб, типов proof и даты — через фильтры `presentation.py`.
- Визуальный стиль — **VKUI** (как BankBot): единый `app/static/css/style.css` с токенами `--vkui-*` (палитра `@vkontakte/vkui` 8), радиусы 8/12/16 px, системный шрифт, высота шапки 56 px, компоненты кнопок, полей, карточек, чипов, бейджей, таблиц, flash и error-страниц.
- Светлая тема по умолчанию, тёмная — через `prefers-color-scheme` (`color-scheme`, два `<meta name="theme-color">`). CSS подключается с версией (`style.css?v=4`), чтобы сбросить старый кэш.
- Адаптивная шапка с мобильным меню, горизонтальный скролл таблиц, видимый фокус, skip-link, `aria`-атрибуты таблиц, `prefers-reduced-motion`.
- Навигация: «Достижения» и «Игроки» (D29). `/leaderboard` содержит только игроков; в каталоге рядом с поиском находятся чипы «Все» и «Только оценённые», а сортировка по умолчанию — «По рейтингу».
- `static/js/main.js` — мобильное меню, pre-check размера файла (4 МБ), подтверждение необратимых действий, кнопка обновления на 503, раскрытие полей заявки в топ-5 по чекбоксу (`data-top5-claim-toggle`).

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
- `024_backfill_ranked_threshold.sql` — backfill: опубликованные достижения с `evaluation_count >= 2`, оставшиеся в `unknown` до снижения порога, переводятся в `ranked`, затем `recompute_ranks_internal()` расставляет места по BT.
- `025_bt_uncertainty.sql` — колонки `bt_rating_se/bt_rating_ci_low/bt_rating_ci_high`, guard новых полей, обновлённый RPC `apply_bt_ratings(jsonb)` с записью интервалов.
- `026_public_completion_proofs.sql` — RPC `achievement_completers` и `user_completed_achievements` начинают возвращать `proofs` (id, тип, описание, путь, дата) для approved-прохождений опубликованных достижений.
- `027_top5_temporary_rating.sql` — система предварительного топ-5: таблицы `top5_applications`, `top5_evaluations`, `top5_expert_requests`, `top5_events`, `achievement_temporary_ratings`, enum-типы, RLS, guard-триггеры (независимость голосов, запрет самоодобрения, один голос эксперта), RPC подачи/решения/отзыва/оценки и `apply_temporary_ratings(jsonb)`, вьюха `public.public_achievements` с `permanent_rank`, `display_rank`, `is_temporary`, `in_leaderboard`. Приложение использует только заявки, решение модератора и временные силы; таблицы экспертных приглашений оставлены для совместимости.
- `028_proof_visibility.sql` — `achievement_completions.proofs_visible` (`NOT NULL DEFAULT true` — доказательства видимы, пока автор не скрыл), триггер «верификация создателя всегда видима» и обновлённые публичные RPC, которые не отдают proofs скрытых прохождений никому, кроме владельца и модераторов.
- `029_submit_evaluation_position_clamp.sql` — `submit_evaluation` зажимает позицию по числу других оценок пользователя (`count(*)`), а не по `max(position)`: при редактировании не-последней оценки остальные сжимаются и «дыра» в нумерации не возникает. Инварианты 022 сохранены. **Применена к проду 2026-11-17.**
- `030_top5_apply_upsert.sql` — `apply_temporary_ratings` вместо очистки всего кэша делает адресное удаление строк, потерявших eligibility (approved + published + `evaluation_count < 3` + `rank is null or rank > 5`), и upsert `on conflict (achievement_id) do update`: stale payload не стирает свежие строки одобренных заявок. Advisory lock и service-role-check сохранены. **Применена к проду 2026-11-17.**
- `031_top5_completion_claims.sql` — заявка в топ-5 вместе с прохождением: `top5_applications.completion_id` (FK на `achievement_completions`, `on delete set null`) + partial index, `evidence_url` становится необязательным (CHECK допускает null/пусто/http(s)), guard INSERT разрешает заявку автору достижения **или** владельцу прохождения, RLS-insert обновлён соответственно, backfill старых активных заявок по approved-прохождению автора. **Применена к проду 2026-11-17** (валидация `BEGIN/ROLLBACK`, HTTP 201; live-проверка guard/RLS в откатываемой транзакции: заявка от владельца прохождения создаётся, без `completion_id` от не-автора — `NOT_AUTHOR`).
- `032_content_refactor.sql` — контент-рефактор под школьную аудиторию (D19): русские названия категорий с сохранением slug, новые категории «Творчество»/«Спорт и здоровье»/«Дом и быт», формализованные тексты достижений. **Применена к проду 2026-11-17.**
- `033_lthub_password.sql` — пароль LTHub-аккаунта в GAL (D22): `external_identities.lthub_login` (логин пользователя в LTHub — нужен для проверки пароля через API LTHub) и `external_identities.password_synced` (пароль служебного Supabase-пользователя подтверждён пользователем, поэтому OAuth-вход его не перевыпускает). Права и RLS не меняются. **Применена к проду 2026-11-23.**
- `034_creator_top5_claim.sql` — заявка в топ-5 при создании достижения (D24): guard INSERT разрешает автору заявку на своё ещё не опубликованное (`pending`) достижение — только с его собственным прохождением (`completion_id`); RLS-insert-политика повторяет правило; для чужих достижений и прочих статусов правила прежние (`published` + автор ИЛИ владелец прохождения). Экспертные таблицы не меняются. **Применена к проду 2026-11-24.**
- `035_completion_comment.sql` — комментарий к прохождению (`achievement_completions.comment`, ≤2000), RPC `set_completion_comment` и `remove_completion_proofs`, публичные RPC прошедших пересозданы с колонкой `comment`. **Применена к проду 2026-11-24.**
- `036_submission_fingerprints.sql` — технический запрет идентичных заявок (D25): `submission_attempt`, `proofs.content_hash`, реестр `submission_fingerprints`, канонизация текста/URL, BEFORE-триггеры achievement/top5/report, service-role RPC `submit_completion_with_proofs`, backfill и `refresh_legacy_submission_fingerprints()`, отзыв прямых INSERT/старых RPC. **Применена к проду 2026-11-26** (перед применением выполнен `scripts/backfill_proof_hashes.py`).
- `037_content_text_clarification.sql` — контентная правка формулировок семи достижений (D26); применялась частями через PostgREST (7 UPDATE, HTTP 204).
- `038_proof_description_not_null.sql` — исправление RPC `submit_completion_with_proofs`: описание доказательства сохраняется пустой строкой, а не NULL (D27). Раньше `nullif(btrim(...), '')` писал NULL в колонку `proofs.description` (`NOT NULL DEFAULT ''`) — любая заявка без описания доказательства падала с SQLSTATE 23502, и приложение показывало безликое «Не удалось отправить заявку». Схема, RLS и права не меняются, переопределяется только функция. **Применена к проду 2026-11-26.**
- `039_submission_content_rule.sql` — правило приёма заявки (D28): `submit_completion_with_proofs` отклоняет пустой набор доказательств, если в сохраняемых первых 2000 символах `p_comment` нет непробельного текста. Достаточно файла, ссылки или комментария; пустая и whitespace-only заявки дают `PROOF_REQUIRED`. Схема, RLS и права не меняются, переопределяется только функция; инвариант доказательства автора (`enforce_creator_proof_on_publish`, 013) не затронут. **Применена к проду 2026-11-27** через Management API (HTTP 201) после валидации в откатываемой транзакции; postflight подтвердил service-only ACL, RLS и creator-trigger, behavior-smoke в откате прошёл, тестовых строк не осталось. Код D28 ещё не закоммичен и не задеплоен.
- `040_player_search.sql` — поиск игроков (D29): `player_leaderboard_search_page(p_limit, p_offset, p_query)` с literal case-insensitive поиском по `username`/`display_name`, `total_count`, сортировкой по очкам и нику, ограничением `limit` 1–100 и `notify pgrst, 'reload schema'`; EXECUTE только у `anon`/`authenticated`. **Подготовлена, но к проду не применялась** по указанию владельца; до применения работает Python-fallback на legacy RPC.
- `041_player_score_bt_rating.sql` — очки игроков относительно глобального максимума BT-рейтинга (D30): internal `player_leaderboard_scores()` и совместимые legacy/page/search RPC; максимум даёт 1000 очков, разница 400 рейтингов уменьшает награду в 10 раз. Временные и неранжированные достижения не дают очков. **Подготовлена, но к проду не применялась.**

**Порядок критичен: 008 → 009 → 010 → 011 → 012 → 013 → 014 → 022 → 025 → 027 → 028 → 029 → 030 → 031 → 032 → 035 → 036 → 038 → 039 → 040 → 041** (008, 022 и 025 содержат `create or replace` функций ранжирования/BT; после 008 обязательно переприменение 010/011/014, после 022 — итоговая версия, 025 должна идти последней из старых и переопределяет guard и `apply_bt_ratings`; 027 добавляет вьюху и временный слой, не меняя постоянный алгоритм; 028 добавляет видимость proofs и переопределяет публичные RPC; 029 переопределяет `submit_evaluation`, 030 — `apply_temporary_ratings`, 031 — guard и RLS заявок топ-5; 032 — только контент, от порядка не зависит; 035 добавляет comment-proof RPC, 036 переопределяет proof-триггер и отзывает права — поэтому идёт после 035; 038 и 039 переопределяют `submit_completion_with_proofs` и обязаны идти после 036, иначе баг с NULL в описании или старое правило обязательного proof вернутся; 040 добавляет RPC поиска игроков после legacy-функций лидерборда; 041 переопределяет все player leaderboard RPC общей BT-формулой).

## Данные

```text
profiles
categories
achievements
achievement_completions
proofs
submission_fingerprints       # реестр отпечатков всех типов заявок (D25/036)
evaluations
moderation_reviews
reports
top5_applications
top5_evaluations
top5_expert_requests
top5_events
achievement_temporary_ratings
```

**Производные значения** (не канон): `achievements.rank`, `bt_strength`, `bt_rating`, `bt_comparisons`, `bt_rating_se`, `bt_rating_ci_low`, `bt_rating_ci_high`, `average_position`, временные силы и `display_rank`, суммы очков игроков — пересчитываются из первичных данных (`evaluations`, `top5_evaluations`).

Новые служебные поля D25: `achievement_completions.submission_attempt` (номер попытки, `>= 0`) и `proofs.content_hash` (SHA-256 содержимого файла или канонической ссылки). Они не являются рейтинговыми данными; реестр отпечатков хранит историю попыток и не удаляется при отклонении/отмене заявки.

## Главная цепочка

```text
Achievement → Creator Proof → Moderation → Published
   → User Completion → Completion Proof → Moderation
   → Personal Evaluation → Pairwise Comparisons
   → Bradley–Terry Strength → Global Rank
   → BT-relative Player Score → Player Leaderboard
```

## Ключевые правила

- Достижение публикуется только после подтверждения собственного выполнения создателем (закреплено триггером 013).
- Оценка доступна только обладателю `approved` completion; одно достижение = одна оценка.
- Минимум 2 оценки для попадания в глобальный рейтинг (иначе `?`); после этого новые оценки продолжают менять топ.
- Одобренная заявка топ-5 показывает временную позицию со `*`; на третьей независимой обычной оценке достижение становится постоянным, `*` снимается, место пересчитывается.
- Временные позиции могут занимать слоты топ-5 и сдвигать постоянные, но не делают чужие постоянные места временными; общая нумерация одна.
- Обычные оценки и решения модератора разделены: ручное место модератора не попадает в `evaluations` и не увеличивает `evaluation_count`.
- Место определяется силой Bradley–Terry; при равной силе — средней позицией, затем зафиксированным `rank_order` и id.
- Очки игрока = сумма наград за уникальные подтверждённые ranked-достижения; награда = `round(1000 · 10^((bt_rating - max_bt_rating) / 400), 2)`, где `max_bt_rating` — глобальный максимум среди eligible-достижений.
- Достижения со статусом `unknown` (`?`) и чисто временные позиции топ-5 дают 0 очков.
- В расчёт идут только достижения с `permanent_rank` и `bt_rating`; временный слой не подменяет постоянное ранжированное достижение.
- Абсолютно идентичную заявку отправить технически невозможно: уникальный отпечаток в БД, а не проверка в UI.
- Одинаковое доказательство нельзя приложить дважды к одному прохождению (`proofs_completion_content_unique`).
- Отклонённую заявку можно переотправить только с изменённым содержимым; прохождение и топ-5 из одной формы не считаются дубликатами друг друга.

## Жизненные циклы

- Achievement: `pending → published` / `rejected`; `deleted` — автором (pending) или модератором.
- Completion: `pending → approved` / `rejected`; после 036 каждая отправка увеличивает `submission_attempt`, заменяет proofs и регистрирует новый отпечаток. Повтор после `rejected` возможен только с изменёнными параметрами/доказательствами; точный повтор даёт `DUPLICATE_SUBMISSION`. Отпечаток сохраняется и после отклонения, отмены или удаления proof. Achievement/top5/report — одноразовая регистрация в реестре; правка своего pending-достижения с тем же текстом разрешена.
- Evaluation: редактируется в любой момент (в т.ч. после `ranked`); новые пользователи добавляют новые оценки, топ пересчитывается.
- Report: `pending → accepted` / `rejected`.
- Top-5 application: создаётся вместе с прохождением (`pending`) → `approved` (временная позиция, кнопка «Принять как сложное») / `rejected` (с причиной; в т.ч. при обычном принятии или отказе прохождения) / `cancelled` — автором или модератором. Независимая экспертиза отключена; legacy-статус `expert_review` показывается как «На рассмотрении» и доступен для решения модератора. На третьей обычной оценке временная запись снимается автоматически (`converted`).

## Развёртывание

- Vercel: `vercel.json`, ASGI = `app.main:app`.
- Переменные: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, опц. `SUPABASE_TIMEOUT_SECONDS`, `APP_ENV=production`, `APP_URL`, `BT_BOOTSTRAP_REPLICATES`, `BT_BOOTSTRAP_SEED`.
- `SERVICE_ROLE_KEY` только на сервере. После 036 он обязателен и для отправки прохождения: RPC `submit_completion_with_proofs`, чтение состояния заявки service-ролью и подпись/publish-пути proofs.
- После применения 036 на существующей БД запускается `python scripts/backfill_proof_hashes.py` — заполняет SHA-256 старых файлов и пересчитывает provisional-отпечатки через `refresh_legacy_submission_fingerprints()`.

## Качество

- `pytest` — 456 тестов на завершённом снимке D29: юнит-тесты ранкинга и Bradley–Terry (включая инвариант «каждая пара = одно соревнование», детерминизм и краевые случаи бутстрэпа, midpoint-силу временного топа с краями и несколькими временными), статические инварианты итоговых определений миграций (BT-сортировка, двухфазный bump, порог 2, живые оценки, guard и запись SE/CI, независимость и уникальность голосов топ-5, переход на третьей оценке, вьюха и service-role-only RPC, видимость proofs и запрет скрытия верификации создателя, привязка заявки к прохождению и guard 031), отказоустойчивость входа по нику и отсутствие утечки email, роуты, CSRF, resilience, OAuth (клиент и провайдер), «Мои заявки», сложность/сравнения, загрузка proof-файлов и ошибки Storage, блокировка создания достижения без оценки, валидация email и маппинг ошибок регистрации, публичные профили и списки прошедших, заявка в топ-5 при прохождении и три решения модератора, hardening (XSS в ссылках, security-заголовки, HTML-ошибки валидации, транзиентные сбои auth, LTHub-аккаунты), статические инварианты 036 (уникальный ключ реестра, advisory lock, service-only RPC, порядок proof-триггера после backfill, service-role-only гранты, вызов `refresh_legacy_submission_fingerprints`), `tests/test_submission_dedupe.py` (38 тестов: маппинг `23505`/`DUPLICATE_SUBMISSION`, атомарная отправка, проверка миграции и кэша схемы, потеря ответа RPC, очистка старых файлов, понятные сообщения во всех формах), инварианты исправления 038 и OR-правила 039, поиск игроков с literal `%`/`_`, fallback по `PGRST202`, пагинация и сохранение параметров, фильтр `rating=rated` до пагинации) и сценарии v1.1–v1.18. Целевой набор D29 — **24 passed**; повторный полный прогон общего рабочего дерева 2026-11-28 — **445 passed / 11 failed** из-за параллельных незавершённых правок профилей и скоринга (миграция 041), не из-за маршрутов D29.
- `ruff check` / `ruff format --check`; CI в `.github/workflows/ci.yml`.
- Live-e2e: `scripts/e2e_evaluation_sql.py`, `scripts/e2e_reports_sql.py`; эксперимент BT — `scripts/bt_experiment.py`.

## Статус

Документация соответствует подготовленному состоянию **v1.18 (D29)** и миграциям **001–040**: 001–039 применены к проду (036 — 2026-11-26, 038 — 2026-11-26, 039 — 2026-11-27), миграция 040 **подготовлена, но не применялась**. Вкладка «Рейтинг» заменена на «Игроки» с поиском; каталог достижений получил `rating=all|rated` без изменения сортировки по сложности. Код D29 не закоммичен, не запушен и не задеплоен по указанию владельца. Результат на завершённом снимке D29 — `pytest` **456 passed**, Ruff чист; на текущем общем дереве целевые тесты D29 — **24 passed**, а полный прогон удерживают параллельные правки профилей/041. Добавлены технический запрет абсолютно идентичных заявок, реестр отпечатков `submission_fingerprints`, service-role RPC `submit_completion_with_proofs`, серверный backfill SHA-256 старых доказательств и приём заявки с файлом, ссылкой или комментарием. Отправка прохождения идёт через service role; прямой INSERT в `achievement_completions` и `proofs` у `authenticated` отозван. Реализация:

 каркас, аутентификация и сброс пароля, вход через LTHub (OAuth + PKCE) и пароль LTHub-аккаунта в GAL (D22), GAL как OAuth-провайдер, достижения + профили + настройки, сложность при создании/прохождении, доказательства и модерация с понятными ошибками Storage и проверкой схемы ссылок, видимость доказательств (скрытие автором, верификация создателя всегда публична, модераторы видят скрытые), движок оценок с порогом 2, запрет создания достижения без оценки опубликованного достижения, глобальный ранкинг Bradley–Terry (каждая пара личной шкалы — отдельное соревнование) с SE и 95% доверительным интервалом, лидерборды с пагинацией и предварительными позициями, система предварительного топ-5 (заявка чекбоксом при прохождении, три решения модератора в общей очереди, midpoint-сила между соседями, `#3*`, переход на третьей оценке), публичные профили с пройденными достижениями и доказательствами, карточки достижений со списком прошедших, их доказательствами и кнопкой отправки прохождения, жалобы, «Мои заявки», CSRF/security-заголовки/HTML-ошибки, адаптивный UI в стиле VKUI (light/dark), CI. Обновлять при изменениях архитектуры, маршрутов и модулей.
