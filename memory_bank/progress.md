# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **100%** (D1–D13 completed).

## Deliverables статус

| ID  | Deliverable                       | Status    |
|-----|-----------------------------------|-----------|
| D1  | Foundation + Memory Bank          | completed |
| D2  | Database Schema + RLS             | completed |
| D3  | Authentication + LThub OAuth      | completed |
| D4  | Achievements CRUD                 | completed |
| D5  | Proofs + Moderation               | completed |
| D6  | Evaluation + difficulty flow     | completed |
| D7  | Ranking Engine (тесты)            | completed |
| D8  | Player Score + Leaderboard        | completed |
| D9  | Reports                           | completed |
| D10 | Polish + hardening (UI, security, errors) | completed |
| D11 | Bugfix по итогам bug-hunt 09-28   | completed |
| D12 | v1.1: функции, безопасность, CI   | completed |
| D13 | v1.2: Bradley–Terry ранкинг       | completed |

## Changelog

### 2026-11-11 — v1.7: публичные списки прошедших и проверка BT

**Запрос.** Убедиться, что каждое сравнение — соревнование BT; публичная страница игрока показывает его публичные данные и пройденные достижения; карточка достижения показывает место, BT-силу, всех прошедших и кнопку отправки прохождения.

**BT.** Аудит `app/services/bradley_terry.py`: `build_comparisons` создаёт по одному `Comparison` на каждую пару внутри личной шкалы (вес `1/(k-1)`), `fit_bradley_terry` учитывает каждое в `wins`, `losses`, `pair_weight` и `comparisons`. Добавлен тест-инвариант: сумма побед/поражений равна суммарному весу всех сравнений, а `sum(comparisons) == 2 × len(comparisons)`.

**Публичные списки.** RLS `achievement_completions` пускал только владельца и модератора, поэтому добавлена миграция `023_public_completions.sql`:
- `achievement_completers(bigint)` — approved-заявки опубликованного достижения: `user_id`, `username`, `display_name`, `avatar_url`, `approved_at` (до 500);
- `user_completed_achievements(uuid)` — approved-заявки игрока по опубликованным достижениям: id, title, rank, ranking_status, bt_rating/strength, evaluation_count, категория, approved_at (до 500).
Обе — `security definer`, `stable`, `search_path=public`, execute у `anon`/`authenticated`; proofs, email, pending/rejected не раскрываются. В сервисе ошибка RPC/сети ловится и даёт пустой список, чтобы страница не падала до применения миграции.

**Страницы.**
- `GET /users/{username}` (доступен анонимно): добавлены очки (`sum(1000/rank)` по ranked), число пройденных, дата регистрации, таблица «Пройденные достижения» (место, BT-рейтинг, категория, дата подтверждения) и секция «Созданные достижения».
- `GET /achievements/{id}`: список «Прошедшие пользователи» с аватарами/профилями и датой; форма отправки переименована в «Отправить прохождение» (`submit_label` тоже).
- A11y: таблицам добавлены `tabindex=0 role=region`, списку — семантический `<ul>`.
- Мелочь: подсказка о ранге исправлена с `< 3` на `< 2` оценок.

**Тесты.** `tests/test_bradley_terry.py::test_every_pair_is_one_contest`; `tests/test_achievements.py` — публичный профиль с пройденными достижениями и карточка со списком прошедших; conftest по умолчанию мокает новые RPC (тесты не ходят в сеть). Итого **206 passed**, ruff check/format чисты (добавлен тест кнопки «Отправить прохождение» для авторизованного пользователя).

**Прод-проверка.** Миграция 023 применена к проду 2026-11-11 (`python -m scripts.apply_migrations 023`, HTTP 201). RPC `achievement_completers(33)` вернул 2 строки, `user_completed_achievements(TonyBig)` — 1. Коммит `3d9bf0c` запушен, CI success, Vercel задеплоил за ~40 с. Live: `/achievements/33` — 200, список из 2 прошедших, место и BT-сила, анониму — CTA входа для отправки; `/users/lucasteam` и `/users/TonyBig` — 200, списки пройденных непустые, статистика на месте. **Не применена** миграция 022 (порог 2 + CHECK ссылок).

### 2026-11-10 — GAL как OAuth-провайдер для регистрации/входа в LTHub

**Запрос.** «При регистрации добавь возможность импорта данных из GAL» — импортировать только поля, которые требует регистрация LTHub, через OAuth «Вход через GAL».

**GAL.** Добавлены `app/services/oauth_provider.py` (клиенты, одноразовые коды TTL 5 минут, opaque-токены 30 дней, SHA-256-хеши, PKCE S256, профиль) и `app/routers/oauth.py`: `GET/POST /oauth/authorize` (consent; анонимного ведёт на `/auth/login?next=`), `POST /oauth/token`, `GET /oauth/userinfo`, `GET /.well-known/oauth-authorization-server`. Consent-шаблон — `app/templates/oauth/authorize.html`; `auth.login` принимает и сохраняет безопасный `next`. Клиенты — статический реестр в коде: `lthub` (публичный, без `client_secret`) + redirect URI прод/localhost, расширяются `OAUTH_LTHUB_REDIRECT_URIS`; таблицы `oauth_clients` нет. Конфиг: `OAUTH_PROVIDER_ENABLED`, `OAUTH_LTHUB_CLIENT_ID`, `OAUTH_LTHUB_REDIRECT_URIS`. Миграция `021_oauth_provider.sql` создаёт `oauth_codes`/`oauth_tokens` (RLS, доступ только `service_role`).

**LTHub (BankBot).** `core/gal_oauth.py` — OAuth-клиент: `GET /auth/gal` (state/PKCE-verifier/`next` в HttpOnly-cookie), `GET /api/auth/gal/callback` (проверка state, обмен кода, `/oauth/userinfo`, поиск/создание `web_users` по `gal_subject`), `GET /auth/gal/done` + `GET /api/auth/gal/session` (одноразовая передача токена в `localStorage.web_token`). Импортируются `preferred_username` → `login` и `display_name`; занятый логин получает суффикс, OAuth-only аккаунт — случайный неиспользуемый хеш пароля; email не импортируется. Кнопки на `/register` и `/login`; `redirect` сохраняется.

**Parity обратного входа (GAL через LTHub).** Обратный поток уже существовал; доработан без переписывания. `lthub_auth.start` пишет `next` в HttpOnly-cookie `gal_lthub_next` через `_safe_next` (только same-origin путь, `/auth/login|logout|register|lthub` отсекаются) и добавляет `intent=register`; callback возвращает на сохранённый `next`. `auth.register_page`/`auth.register` принимают и сохраняют `next`, редирект после успешной регистрации. Шаблоны `login.html`/`register.html` передают `next`, кнопка регистрации — `intent=register`. LTHub `core/oauth.py`: `intent=register` переключает authorize-страницу на `/register?redirect=<authorize URL>` для незалогиненных; `doRegister` валидирует redirect (закрыт open redirect). Тесты: GAL `test_lthub_oauth.py` +3 (safe/unsafe next, intent, redirect после callback), `test_auth.py` +3; LTHub `test_oauth_provider.py` +2 (intent=register, неизвестный intent → login).

**Проверки.** GAL — `tests/test_oauth_provider.py` (8 тестов), всего **202 passed**, ruff check/format чисты; целевые после parity — **52 passed** (`test_lthub_oauth.py`, `test_auth.py`, `test_oauth_provider.py`). LTHub — `tests/unit/test_gal_oauth_client.py` (7) + `test_oauth_provider.py` (6) **13 passed**, целевой регресс (`test_web_portal_e2e`, `test_gal_oauth_client`) **39 passed**, ruff чист; smoke `/register` и `/login` — 200 с кнопками GAL.

**Не применено.** Миграция `021_oauth_provider.sql` к проду **не применена** (общая инфраструктура, требуется подтверждение владельца) — без неё OAuth-вход в LTHub вернёт ошибку.

### 2026-11-10 — v1.6: hardening, устранение регрессий и защита ранжирования

**Запрос.** «Улучшай проект насколько возможно».

**Аудит.** Три субагента просмотрели безопасность, миграции/БД и UX/a11y; находки проверены вручную.

**Безопасность.**
- Stored XSS в ссылках-доказательствах: `app/services/completions.py::safe_external_url` допускает только `http(s)://`, применяется при attach и в `proof_view`; миграция 022 добавляет `CHECK proofs_link_scheme` (defense-in-depth).
- `app/middleware.py`: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Content-Security-Policy: frame-ancestors 'none'; base-uri 'self'; form-action 'self'`, HSTS в проде.
- `app/services/lthub_accounts.py`: при ошибке `create_user` пользователь ищется только по точному email (раньше — по username, что могло привязать LTHub-identity к чужому профилю).
- `app/services/auth.py`: транзиентный сбой Supabase больше не удаляет cookies — разлогин только при явно невалидной сессии.

**Целостность миграций.** Удалена `020_reduce_evaluation_threshold.sql` (переопределяла `recompute_ranks_internal()` без сортировки по BT, без двухфазного bump и без `security definer`; `submit_evaluation` без `NOT_FOUND`/advisory lock; `LOCKED`/безусловный `random()`), дублировала номер 020 и ломала чистую БД. Добавлена `022_restore_ranking_and_proof_scheme.sql`: канонический пересчёт (BT + двухфазный bump + `security definer`), `submit_evaluation`/`apply_completion_evaluation` с порогом 2, `NOT_FOUND`, advisory lock, `rank_order = coalesce(rank_order, random())`, CHECK на схему ссылок. `scripts/apply_migrations.py` теперь падает с понятной ошибкой при дубле номера.

**Корректность и UX.**
- HTML-страницы при `RequestValidationError` получают HTML-422 с русским текстом (раньше — сырой JSON); 5xx не раскрывают `exc.detail`.
- Pending-заявка без доказательства больше не тупик: форма повторного прикрепления на карточке.
- При переотправке отклонённой заявки старые proof-файлы удаляются только после успешного attach (раньше — до, и терялись при сбое).
- Сортировка «Новые» исправлена (reverse применялся ко всему списку): новые сверху, ranked выше unknown.
- `ROLE_LABELS` + фильтр `role_label`; порог в UI «после 2 оценок».
- A11y: flash `role=alert/status`, таблицы с `tabindex=0 role=region`, `<textarea>` + `<label>` для причин, `data-confirm` на approve, чтобы Enter не одобрял заявку.
- `.gitignore`/`ruff.toml`: одноразовые скрипты исключены из линта; `.env.example` дополнен `SUPABASE_TIMEOUT_SECONDS`, `DISPLAY_UTC_OFFSET_HOURS`.

**Тесты.** Новый `tests/test_hardening.py` (10): XSS-схемы URL, security-заголовки, HTML-422, сетевой сбой не разлогинивает, невалидная сессия разлогинивает, поиск LTHub-пользователя по email. Итого **196 passed**, ruff check/format чисты.

**Не применено к проду.** `022_restore_ranking_and_proof_scheme.sql` требует подтверждения владельца: меняет порог ranked 3→2 и добавляет CHECK на proofs. До применения прод-функция `submit_evaluation` остаётся с порогом 3.

### 2026-11-09 — Вход по никнейму или email

**Запрос.** После регистрации пользователь (`TonyBig`) отметил, что вход просит email, а не никнейм.

**Реализовано.** `app/services/auth.py::resolve_login_email(identifier)`: значение с `@` — email (возвращается как есть); иначе ник резолвится в email на сервере через service role — `profiles.ilike(username)` (регистронезависимо, `ilike_literal`) → `auth.admin.get_user_by_id(id).user.email`. Email не утекает клиенту. Неизвестный ник, пустое значение или недоступный service role → `None`. `POST /auth/login` вызывает резолвер; при `None` показывает общее «Неверный email или пароль» (то же, что при неверном пароле — без раскрытия существования ника). Форма `auth/login.html`: поле `type=text` (ник проходит браузерную валидацию), label «Email или никнейм», `autocomplete=username`, `autocapitalize=none`, плейсхолдер «TonyBig или name@example.com», подсказка. Имя поля `email` сохранено для совместимости с JSON-потребителями.

**Тесты.** `tests/test_auth.py`: passthrough email/пусто, ник без service role → None, ник найден (мок service-клиента, проверяется `get_user_by_id`), ник не найден → None, вход по нику резолвится и логинит (303 + cookie), неизвестный ник → общее сообщение. Итого **178 passed**, ruff check/format чисты по отслеживаемым файлам.

**Деплой и прод-проверка.** Запушено `7dd4069` → автодеплой Vercel; CI success (Ruff lint/format/Tests). Прод-E2E в браузере (`gal-inky.vercel.app/auth/login`): поле «Email или никнейм» (`type=text`); `TonyBig` + неверный пароль → «Неверный email или пароль», `invalidEmail=false` (ник резолвится в email, иначе GoTrue вернул бы «Некорректный email»); неизвестный ник → то же общее сообщение. На проде аккаунт `TonyBig` / `tonybig11200@gmail.com` подтверждён (`email_confirmed_at` не null).

### 2026-11-09 — Исправление регистрации (3 ошибки) и разблокировка CI

**Запрос.** Сайт не давал зарегистрироваться: «username tony занят», «retry in 20s», «invalid email». Исправить все три и задеплоить.

**Диагноз (Management API).** Аккаунт `tony` / `tonybig1120@gmail.com` создан 2026-11-09 13:30, `email_confirmed_at=null`, `last_sign_in_at=null` — триггер `handle_new_user` создал профиль сразу при sign_up, поэтому username держал собственный неподтверждённый аккаунт пользователя (данных 0). Конфиг Auth: `mailer_autoconfirm=false`, `rate_limit_email_sent=2` (встроенный SMTP, 2 письма/час). Повторные sign_up слали письмо подтверждения → GoTrue 429 «For security purposes, you can only request this after 20 seconds»; «invalid email» — сырая ошибка валидации GoTrue. Обе показывались пользователю сырым английским текстом (`_error_message` возвращал `exc.message` как есть).

**Реализовано.**
- Supabase Auth: `PATCH /config/auth` → `mailer_autoconfirm=true`. Регистрация сразу возвращает сессию и логинит (ветка `session is not None` → redirect `/` + cookie), без писем подтверждения и email-rate-limit. Встроенный SMTP Supabase не предназначен для прода — подтверждение письма было источником всех трёх симптомов.
- Зависший аккаунт `tony` (`084d5355…`, 0 достижений/заявок/оценок/жалоб) удалён `delete from auth.users where id=… and email_confirmed_at is null` (каскадно чистит профиль). Username `tony` и email свободны для пользователя.
- `app/services/auth.py`: `validate_email` (`EMAIL_RE = ^[^@\s]+@[^@\s]+\.[^@\s]+$`, длина ≤254) до обращения к GoTrue; `friendly_auth_error(exc)` маппит коды/сообщения GoTrue в русские тексты: rate limit (`over_*_rate_limit`, «after N seconds» → «Повторите через N сек»), `email_address_invalid`/`validation_failed` → «Некорректный email», `email_exists`/`user_already_exists` → «email уже зарегистрирован», `weak_password`, `invalid_credentials`, `email_not_confirmed`, `signup_disabled`; неизвестное → общий текст.
- `app/routers/auth.py`: `_error_message` логирует сырую ошибку и возвращает `friendly_auth_error`; register добавляет серверную валидацию email и сохраняет значения полей при ошибке (ранее username очищался при «занято»).

**Тесты.** `tests/test_auth.py`: правила `validate_email`, маппинг rate-limit/invalid-email/email-exists/unknown, регистрация с невалидным email, мгновенный вход при autoconfirm-сессии (303 + cookie); login-JSON тест переведён на реалистичный `AuthApiError(invalid_credentials)`. Итого **172 passed**, ruff check/format чисты по отслеживаемым файлам.

**CI.** Был красный с `62aec27` (не связан с регистрацией): I001 в `app/main.py` (алиасный импорт `completions as completions_router` требовал отдельного блока) + формат `app/routers/completions.py` (пробелы/перенос). Исправлено `ruff --fix`/`ruff format` (коммит `2b89151`). Локальный ruff читает пользовательский конфиг с extra-правилами (I/YTT/BLE), которого нет в репозитории; отслеживаемые файлы чисты и по дефолту, и по строгим правилам.

**Деплой и прод-проверка.** Запушено `5691eef` (registration fix) + `2b89151` (CI lint/format) → автодеплой Vercel. Прод-E2E в браузере (`gal-inky.vercel.app`): POST регистрации с невалидным email отдаёт новое русское сообщение (новый код жив); регистрация `e2e_regcheck` / `e2e.regcheck.gal@example.com` → редирект `/`, мгновенный вход, `/auth/me` вернул корректный профиль (username/display_name/email/role). Тестовый аккаунт удалён; на проде только `lucasteam`, `tony` свободен (`tony_taken=0`, `tony_email_exists=0`), `mailer_autoconfirm=true`.

### 2026-11-08 — Создание достижения только после оценки существующего

**Запрос.** Новый пользователь не должен создавать своё достижение, пока не прошёл и не оценил хотя бы одно уже существующее.

**Правило и UI.** `GET /achievements/create` и `POST /achievements/create` проверяют `has_user_evaluation` до валидации полей. При блокировке форма не рендерится: показывается объяснение и ссылка «Перейти к достижениям»; категории из БД не запрашиваются. `has_user_evaluation` теперь считает только оценки достижений со статусом `published` — оценки удалённых/скрытых достижений права на создание не дают. В `create_achievement` добавлен defense-in-depth guard (`PermissionError` с текстом `CREATE_REQUIRES_EVALUATION`), чтобы правило нельзя было обойти другим вызовом сервиса.

**RLS.** `migrations/020_creation_requires_evaluation.sql`: security-definer функция `public.user_has_published_evaluation(uuid)` (EXECUTE только у `authenticated`) и обновлённая политика `achievements_insert_own`: insert разрешён только при `creator_id = auth.uid()`, `status = 'pending'` и наличии оценки опубликованного достижения. Supabase default privileges выдают EXECUTE новым функциям роли `anon` явно, поэтому в миграции выполнен `revoke ... from public, anon` (одного `revoke ... from public` недостаточно). Service role обходит RLS.

**Проверка.** Миграция прогнана в rollback-транзакции через Management API: без оценки insert даёт `42501`, с оценкой опубликованного достижения — проходит, `anon` не может вызвать функцию. Применена к проду (201); подтверждено: `anon_can_execute=false`, `authenticated_can_execute=true`, политика содержит `user_has_published_evaluation(auth.uid())`. Тесты: блокировка GET/POST, сервисный guard, фильтр удалённых достижений; итого **165 passed**, `ruff check`/`format` — чисто.

**Очистка прода.** Удалены 4 тестовых аккаунта (`test_player`, `test_eval_1..3`) и их оценки/заявки/proofs. Достижения #35 и #36 сохранены и переназначены на `lucasteam` (по решению пользователя). Сохранённые метрики пересчитаны из `evaluations`, BT пересчитан по оставшимся 3 оценкам через `apply_bt_ratings` (Management API, т.к. локальный PostgREST недоступен). Итог: 1 пользователь (`lucasteam`), 3 оценки, 3 approved-заявки с proofs, лидерборд пуст (все достижения `unknown`).

**Деплой и прод-проверка.** Запушено в `main` (`497eb00` — код и миграция 020, `b9317a3` — Memory Bank); CI run #40 на `b9317a3` — success. Прод: `/health` и `/achievements` — 200; в каталоге видны #31, #33, #35 и #36, в карточках #35/#36 автор `lucasteam`, подписей тестового игрока нет; для анонимного пользователя кнопка создания отсутствует (показывается «Войти»).

### 2026-11-08 — VKUI-оформление и понятные ошибки загрузки доказательств

**Запрос.** Привести визуал GAL к стилю BankBot (VKUI) и исправить ошибку при загрузке файла-доказательства.

**UI.** `app/static/css/style.css` переписан на токенах `@vkontakte/vkui` 8 (`--vkui-*`): светлая тема по умолчанию, тёмная через `prefers-color-scheme`; палитра bg/content/secondary/field/separator/text/accent/positive/negative/warning, радиусы 8/12/16 px, системный шрифт, шапка 56 px. Покрыты все классы из 26 шаблонов: шапка с мобильным меню, hero, шаги, карточки, чипы, бейджи, таблицы, профиль, auth-карточки, flash, error-page, scale-picker, `prefers-reduced-motion`. `base.html` получил `color-scheme`, два `theme-color` и версионирование CSS (`style.css?v=2`). Проверено через CDP: light (`bg #ebedf0`, accent `#2688eb`) и dark (`bg #0a0a0a`, content `#19191a`, accent `#529ef4`), на 390 px нет горизонтального переполнения и наложений.

**Загрузка доказательств.** `app/services/completions.py`: `_storage_error_message` переводит ошибки Storage в понятные тексты (413 → лимит 4 МБ; 401/403/JWT/RLS → войти заново/ссылка; 404/bucket → временно недоступно; иначе общий текст), детали — в лог. `OSError` при чтении файла → «Не удалось прочитать файл. Выберите его заново.». `app/services/auth.py`: `user_client_from_request` берёт токены из `request.state` (обновлённые `resolve_user`), иначе из cookies — иначе после refresh Storage/PostgREST отвечали 401 на том же запросе.

**Правила.** Создание достижения доступно только пользователю с хотя бы одной оценкой (`has_user_evaluation`). Сортировка `unknown`-достижений использует `average_position` без сдвига `-0.5`, чтобы `?`-достижения не вставали перед rank #1.

**Тесты.** Новый `tests/test_proofs.py` (6): успешная загрузка в `{user_id}/{achievement_id}/`, ссылка без Storage, лимит 4 МБ, 403/RLS и 413 → понятные сообщения, multipart через `/achievements/5/complete` доходит до Storage. `tests/test_auth.py`: приоритет обновлённых токенов. Итого **160 passed**, `ruff check`/`format` — чисто.

### 2026-11-07 — v1.5: новые оценки меняют топ

**Правило.** Каждый выполнивший достижение обязан указать сложность при отправке заявки; после approve оценка добавляется. Оценки принимаются и после выхода в `ranked` — топ пересчитывается после каждой новой заявки/оценки.

**Реализовано.** `migrations/019_live_evaluations.sql`: снят lock со всех evaluations; `submit_evaluation` больше не блокирует ranked и не ставит locked; `apply_completion_evaluation` принимает новые оценки для уже ранжированных достижений и пересчитывает среднее/ранги/BT. Python: `can_evaluate` пускает на страницу оценки при approved completion без учёта ranked/lock; карточка и форма снова доступны для ranked; тест ranked-completion обновлён. Тесты — 153 passed.


### 2026-11-07 — v1.4: сложность при создании/прохождении и сравнительный выбор

**Запрос.** Спрашивать позицию достижения в личной шкале уже при создании и при прохождении; поле при прохождении — «Сложность», при верификации — «Ожидаемая сложность»; при оценке не вводить топ-1/2/3, а отвечать «сложнее/легче, чем …».

**Реализовано.**
- Общий виджет `partials/scale_picker.html` + JS: бинарный поиск позиции по ответам «Сложнее/Легче»; показывает, между какими достижениями встанет новое; без номеров.
- Создание достижения: обязательный шаг «Ожидаемая сложность»; значение уходит в `submit_completion`.
- Прохождение существующего: поле «Сложность»; значение сохраняется в `achievement_completions.requested_harder_count`.
- Переотправка отклонённой заявки: значение можно изменить, оно перезаписывается.
- `apply_completion_evaluation(completion_id)` (миграция 017): после approve модератором создаёт обычную оценку автора (сдвиг шкалы, порог 3, lock, пересчёт рангов). Вызывается при approve completion и approve достижения (creator completion).
- Страница оценки: вместо select «№1/№2/…» — тот же сравнительный виджет; текст шкалы без номеров.
- Тесты: `tests/test_difficulty.py` (5) + обновление evaluations; всего **152 passed**, ruff чист.

**Прод.** Миграция 017 проверена в rollback (201) и применена (201); функция `apply_completion_evaluation` проверена в rollback-транзакции (создаёт 1 evaluation, счётчик достижения = 1).

**Дополнение (авто-якорь).** Первую оценку больше не нужно сохранять вручную: `apply_completion_evaluation` при отсутствии оценок у пользователя ставит первую approved-заявку на позицию 1 (даже если `requested_harder_count` null). Миграция 018 применена к проду; backfill автоматически оценил supersonic пользователя (`position=1`), поэтому на странице «Снять шортс про математику» появится сравнение с ним. Тест 152.


### 2026-11-06 — «Мои заявки»: статусы, причины отклонения, переотправка

**Запрос.** Где посмотреть отправленные заявки; добавить раздел с изменениями и переотправкой отклонённых.

**Реализовано.**
- `GET /submissions` — все заявки текущего пользователя: достижение, статус, дата, число доказательств, причина отклонения; фильтры `all/pending/approved/rejected`; ссылка в навигации «Мои заявки».
- `app/services/completions.list_user_completions` — выборка через RLS пользователя + последняя причина отклонения; `get_rejection_reason` для карточки.
- Карточка достижения: при `rejected` показывает причину и форму «Отправить снова» (файл/ссылка/комментарий); при `pending` — статус; ранее форма для rejected была недоступна.
- `POST /achievements/{id}/complete` и `submit_completion` поддерживают переотправку: RPC сбрасывает rejected-заявку, старые файлы удаляются, создаётся новая pending-заявка.
- `migrations/016_completion_review_visibility.sql` — RLS: автор видит решения модерации по своей заявке.
- Тесты: `tests/test_submissions.py` (4) + обновление moderation; всего **146 passed**, ruff чист.

**Прод.** Миграция 016 проверена в rollback-транзакции (201) и применена (201). После деплоя раздел доступен по `/submissions`.


### 2026-11-06 — v1.3: вход через LTHub (OAuth 2.0 + PKCE)

**Зачем.** Пользователь попросил «войти через LThub», используя аккаунты LTHub (`lthub.vercel.app`).

**LTHub (BankBot, коммит `f1746ed`).** Добавлен самостоятельный OAuth-provider `core/oauth.py`, зарегистрированный из `api/index.py`:
- `GET /oauth/authorize` — consent-страница; использует `localStorage.web_token`, при отсутствии сессии ведёт на `/login?redirect=...` (страница логина теперь уважает `redirect`);
- `POST /api/oauth/authorize` — проверяет сессию и параметры, выдаёт одноразовый код (SHA-256 в `oauth_codes`, TTL 5 мин);
- `POST /oauth/token` — authorization_code + PKCE S256, проверка redirect_uri/client_id/verifier, одноразовость, opaque token в `oauth_tokens` (30 дней);
- `GET /oauth/userinfo`, `/.well-known/oauth-authorization-server`;
- клиент `gal` — публичный (без client_secret), redirect URIs: прод GAL + localhost.

**GAL (коммит `3fd8a74`).** `GET /auth/lthub` создаёт `state` + PKCE-verifier в HttpOnly-cookie, редиректит на LTHub; `GET /auth/lthub/callback` проверяет state, меняет код на токен, читает userinfo и логинит/создаёт Supabase-пользователя (`app/services/lthub_accounts.py`). Привязка хранится в `external_identities` (миграция 015, доступ только service_role). Кнопки добавлены на вход/регистрацию; ошибки OAuth показываются на логине.

**Дополнение (username).** Изначально username жёстко строился как `lthub_<subject>` (пользователь увидел `lthub_8`). Исправлено: username берётся из логина LTHub (`shadowraven`), при занятости — суффикс, крайний fallback — `lthub_<subject>`; для уже созданных аккаунтов с дефолтным `lthub_<subject>` ник подтягивается при следующем входе (`_sync_profile`). Прод-E2E подтвердил: `/auth/me` нового входа → `username: shadowraven`, `display_name: ShadowRaven`. Тестовый пользователь удалён; аккаунт пользователя subject 8 (`lthub_8`) не тронут — ник обновится при следующем входе. Тест 141.

**Проверка.** Локально: 4 теста OAuth-provider (BankBot, зелёные), 5 тестов GAL (140 всего), ruff чист. Прод: LTHub `/oauth/authorize` и metadata — 200; полный E2E на проде — LTHub-логин `shadowraven` → authorize → code → GAL callback → `gal_session`; `/auth/me` вернул `lthub_23` / `ShadowRaven`, навигация авторизована. Тестовый GAL-пользователь и identity удалены (`external_identities=0`, `lthub_%` profiles=0). Миграция 015 применена (201).

**Замечание.** Холодный старт LTHub-функции может занимать ~57 с (большое Flask-приложение); повторный E2E-прогон упёрся в лимит Vercel 60 с на холодном старте. Это инфраструктурное ограничение LTHub, не ошибка OAuth-кода; после прогрева authorize отвечает ~1.4 с.


### 2026-11-06 — v1.2 (D13): глобальный ранкинг Bradley–Terry

**Зачем.** `docs/idea.md` задаёт исследовательскую рамку: индивидуальные шкалы → попарные сравнения → модель Bradley–Terry. Прежний ранкинг по средней позиции заменён BT-силой.

**Модель.** Для каждой шкалы из `k` достижений строятся все пары (`A` выше `B` ⇒ `A` сложнее), вес пары `1/(k-1)`. Оценка — регуляризованный MM (Hunter, 2004), prior κ=0.5 против среднего, центрирование `mean(s)=0`; `P(A>B)=σ(s_A-s_B)`, `bt_rating = 1000 + 400·s/ln10`. Реализация — `app/services/bradley_terry.py` (чистый Python).

**Данные и пересчёт.** `migrations/014_bradley_terry.sql`: колонки `bt_strength/bt_rating/bt_comparisons/bt_updated_at`, guard новых полей, `recompute_ranks_internal()` сортирует по `bt_strength desc` с fallback на `average_position`, RPC `apply_bt_ratings(jsonb)` (security definer, только `service_role`, advisory lock). `app/services/bt_recompute.py` читает все оценки service role, считает силы и применяет результат. Автозапуск после оценки, модерации и принятия жалобы; ошибка только логируется. Ручной запуск — `scripts/recompute_bt.py`.

**UI.** Карточка достижения: BT-рейтинг, сила (логит), число попарных сравнений. Лидерборд: колонки BT-рейтинг и сравнения + пояснение шкалы. Порог 3 оценок сохранён.

**Эксперимент.** `scripts/bt_experiment.py` (40 достижений, 20 игр/пару, 15 600 сравнений): Spearman **0.9908**, Kendall **0.9333**, Brier **0.193**, bootstrap Spearman **0.9910** (min 0.9869), top-10 overlap **0.93**. Подробно — `docs/bradley_terry.md`.

**Проверка.** `pytest` — **135 passed**, ruff check/format — чисто. Миграция 014 проверена на проде в транзакции с `rollback` (201): колонки/функция создаются, `apply_bt_ratings` переставляет ранги по BT, anon получает `42501`, `service_role` — 0. Миграция применена к проду (201); объекты подтверждены. Деплой v1.2.0: `/leaderboard` содержит BT-колонки, ключевые маршруты 200. Live-проверка UI: временное достижение с `bt_rating=1217`, `bt_strength=1.25`, 42 сравнениями отобразилось в лидерборде и карточке; данные удалены (`achievements=0`, `profiles=0`).

**Ограничения.** Пары внутри шкалы зависимы; связность графа обеспечивается prior-ом; пересчёт полный (O(N·iter)) — при росте данных нужен батч/материализация; доверительных интервалов пока нет.

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

`3d9bf0c feat: public achievement completers and player pages` (2026-11-11) — закоммичено и запушено в `main`, CI success, Vercel задеплоил v1.7; live-страницы проверены. Миграция `023_public_completions.sql` применена к проду 2026-11-11. Миграции `021_oauth_provider.sql` и `022_restore_ranking_and_proof_scheme.sql` не применены.
