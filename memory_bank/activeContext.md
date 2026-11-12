# Active Context — GAL

## Текущий фокус

**2026-11-12 — D14: надёжность Bradley–Terry (завершено локально).** Реализован кластерный бутстрэп по личным шкалам: SE и 95% percentile CI, тёплый старт MM-реплик, детерминированный seed, адаптивное число реплик и пороги (≥3 шкалы, ≥5 реплик, ≥2 сравнения). Добавлены `bt_rating_se`/`bt_rating_ci_low`/`bt_rating_ci_high` (миграция `025_bt_uncertainty.sql`, **к проду не применена**), расширенные payload/RPC/guard, фильтр `rating_ci` и вывод в карточке достижения и лидерборде. Версия `1.8.0`, `pytest` — **222 passed**, ruff check/format чисты. Изменения не закоммичены и не задеплоены; удалённая БД не изменялась.

**2026-11-12 — защита критичных инвариантов тестами.** После обновления рабочей копии до `311691e` регрессия SQL уже закрыта миграциями 022/024, поэтому продовый код повторно не менялся. Добавлены тесты отказоустойчивости `resolve_login_email` (сбой service role, пустой email), отсутствия утечки email при неверном пароле и статические инварианты итоговых определений миграций: уникальность номеров, BT-сортировка, двухфазный bump, порог 2, живые оценки и пересчёт рангов. Итого **213 passed**, ruff check/format чисты. Изменения не закоммичены и к БД не применялись.

**2026-11-11 — порог ранжирования снижен до 2 оценок.** По запросу пользователя («для попадания в топ нужно 2 оценки, а не 3 из-за недостатка пользователей») к проду применена миграция 022: `submit_evaluation` и `apply_completion_evaluation` теперь переводят достижение в `ranked` при `v_count >= 2`; попутно включён `proofs_link_scheme` (`NOT VALID`, нарушающих строк 0). Добавлена и применена `024_backfill_ranked_threshold.sql`: достижения, уже набравшие 2 оценки до снижения порога, переведены в `ranked` с разными временными `rank` и каноническим `recompute_ranks_internal()`. Результат на проде: #46 (2 оценки) — #1, #33 (2) — #2, #35 (3) — #3; опубликованных `unknown` с `evaluation_count >= 2` больше нет. Ручные e2e-скрипты обновлены под порог 2.

**2026-11-11 — v1.7: публичные страницы игрока и достижения.** Проверены три требования. BT: каждая пара личной шкалы уже входит в модель одним соревнованием (`build_comparisons` → `Comparison`, учитывается в `wins/losses/adjacency`); добавлен тест-инвариант «каждая пара = одно соревнование». Публичная страница `/users/{username}` теперь показывает очки, число пройденных, дату регистрации, список **пройденных** достижений (место, BT-рейтинг, категория, дата) и созданные достижения. Карточка `/achievements/{id}` показывает место, BT-рейтинг/силу, список всех прошедших пользователей (аватар, имя, дата) и форму «Отправить прохождение». Для публичного чтения approved-completions добавлена миграция `023_public_completions.sql` (security definer RPC `achievement_completers`, `user_completed_achievements`; только поля профиля, без proofs/email). Новая функция устойчива к отсутствию миграции/сети — список пуст, страница работает. Версия `1.7.0`, тесты — **206 passed**, ruff чист. Миграция 023 применена к проду 2026-11-11 (`scripts.apply_migrations 023`, HTTP 201): RPC проверены на живых данных (достижение #33 — 2 прошедших, `TonyBig` — 1 пройденное). Коммит `3d9bf0c` запушен в `main`, CI — success, Vercel задеплоил за ~40 с. Live-проверка: `/achievements/33` — 200, «Прошедшие пользователи» с 2 профилями, место/BT-сила, анониму — кнопка входа для отправки; `/users/lucasteam` и `/users/TonyBig` — 200, списки пройденных непустые, статистика (очки/пройдено/дата) на месте. Миграция 022 применена к проду следом 2026-11-11 — порог ранжирования теперь 2.

**2026-11-10 — GAL как OAuth-провайдер для регистрации/входа в LTHub.** По запросу пользователя («при регистрации добавь возможность импорта данных из GAL») LTHub получил кнопки «Зарегистрироваться через GAL» и «Войти через GAL» (OAuth 2.0 authorization code + PKCE S256, публичный клиент `lthub`). В GAL добавлены `app/services/oauth_provider.py` и `app/routers/oauth.py`: `/oauth/authorize` (consent, анонимного ведёт на `/auth/login?next=`), `/oauth/token`, `/oauth/userinfo`, `/.well-known/oauth-authorization-server`; одноразовые коды (TTL 5 минут) и opaque-токены (30 дней) хранятся только как SHA-256-хеши. Клиенты — статический реестр в коде (таблицы `oauth_clients` нет), redirect URI LTHub задаются `OAUTH_LTHUB_REDIRECT_URIS`. Миграция `021_oauth_provider.sql` **к проду не применена** — до её применения кнопки в LTHub работать не будут. Тесты провайдера — `tests/test_oauth_provider.py` (8), всего **202 passed** после добавления parity-тестов, ruff чист. В том же фокусе — parity обратного входа: `lthub_auth.start` сохраняет безопасный `next` в HttpOnly-cookie и добавляет `intent=register` для кнопки регистрации, callback возвращает на `next`; `POST /auth/register` тоже сохраняет `next`; LTHub-провайдер по `intent=register` ведёт незалогиненного на `/register?redirect=<authorize URL>`. Новые тесты — GAL `test_lthub_oauth.py` +3 и `test_auth.py` +3, LTHub `test_oauth_provider.py` +2; целевые прогоны зелёные (GAL 52, LTHub 13).

**2026-11-10 — v1.6: hardening и исправление регрессий.** Проведён аудит (3 субагента) и закрыты: stored XSS в ссылках-доказательствах (`safe_external_url`, рендер + DB CHECK `proofs_link_scheme`); опасная миграция `020_reduce_evaluation_threshold.sql` удалена (переопределяла `recompute_ranks_internal` без BT/двухфазного bump/`security definer`), вместо неё корректная `022_restore_ranking_and_proof_scheme.sql` (порог 2, canonical ranking, CHECK на ссылки); `apply_migrations.py` отклоняет дубли номеров; разлогин при транзиентном сбое Supabase устранён; LTHub-фолбэк больше не ищет пользователя по username (только точный email); добавлены security-заголовки (CSP frame-ancestors, nosniff, HSTS в проде); HTML-422 вместо сырого JSON на страницах; форма повторного прикрепления proof для pending-заявки без файла; исправлена сортировка «Новые»; `role_label`; a11y (flash-роли, таблицы с `tabindex`/`role=region`, label у причин модерации); proof-файлы отклонённой заявки удаляются только после успешного attach. Версия `1.6.0`; тесты — **196 passed**, ruff чист. Миграция 022 к проду **не применена** (требует подтверждения: изменит порог 3→2 и добавит CHECK).

**2026-11-09 — вход по никнейму или email.** Пользователь (`TonyBig` / `tonybig11200@gmail.com`, зарегистрирован после фикса регистрации) пожаловался, что вход просит email, а не ник. Реализован вход по «email или никнейму»: `app/services/auth.py::resolve_login_email` — если в значении есть `@`, это email; иначе ник резолвится в email на сервере через service role (`profiles.ilike(username)` → `auth.admin.get_user_by_id(...).user.email`), email не утекает клиенту. Неизвестный ник / недоступный service role → `None` → роут показывает общее «Неверный email или пароль» (как и при неверном пароле, без раскрытия существования ника). `POST /auth/login` использует резолвер; форма `login.html` — `type=text`, label «Email или никнейм», `autocomplete=username`, плейсхолдер «TonyBig или name@example.com». Тесты — **178 passed**, ruff check/format чисты. Прод-E2E в браузере: поле «Email или никнейм»; `TonyBig` + неверный пароль → «Неверный email или пароль» (invalidEmail=false — ник резолвится в email); неизвестный ник → то же общее сообщение. CI на `7dd4069` — success (lint/format/tests). Запушено `7dd4069`, автодеплой Vercel подтверждён.

**2026-11-09 — исправление регистрации (3 ошибки) и деплой.** Пользователь не мог зарегистрироваться: «username tony занят», «retry in 20s», «invalid email». Диагностика через Management API: аккаунт `tony` / `tonybig1120@gmail.com` создан 2026-11-09 13:30, но `email_confirmed_at=null` (ни одного входа) — триггер `handle_new_user` сразу создал профиль, и username держал собственный зависший аккаунт пользователя. `mailer_autoconfirm=false` + встроенный SMTP с `rate_limit_email_sent=2` письма/час → повторные попытки шлют письмо подтверждения → GoTrue 429 «For security purposes… after 20 seconds», сырой английский текст показывался как есть; «invalid email» — сырая ошибка валидации GoTrue. **Fix:** (1) в Supabase Auth включён `mailer_autoconfirm=true` — регистрация сразу возвращает сессию и логинит, без писем и email-rate-limit; (2) зависший пустой аккаунт `tony` удалён (`delete from auth.users`, данных 0) — username и email свободны; (3) `app/services/auth.py`: `validate_email` (формат `local@domain.tld`) до GoTrue + `friendly_auth_error` (русские сообщения для rate limit/invalid email/already registered/invalid credentials/weak password); роутер registration/login/recover использует маппер вместо сырого текста. **Проверка:** `pytest` — **172 passed**, ruff check/format чисты по отслеживаемым файлам. Прод-E2E в браузере: регистрация `e2e_regcheck` → мгновенный вход (редирект `/`), `/auth/me` вернул корректный профиль; тестовый аккаунт удалён (на проде только `lthub_8`/`lucasteam`, `tony` свободен). CI был красный с `62aec27` (I001 в `main.py` + формат `completions.py`) — исправлено `ruff --fix`/`format` (коммит `2b89151`). Запушено `5691eef` (registration fix) + `2b89151` (CI lint/format); автодеплой Vercel подтверждён (новый код на проде: серверная валидация email выдаёт новое русское сообщение).

**2026-11-08 — создание достижения только после оценки.** Новый пользователь не может предложить своё достижение, пока не прошёл и не оценил хотя бы одно опубликованное. `GET` и `POST /achievements/create` до валидации проверяют `has_user_evaluation`; при блокировке форма скрыта, показывается объяснение и ссылка на каталог. `has_user_evaluation` учитывает только оценки `published`-достижений (удалённые/скрытые не дают права). В `create_achievement` — defense-in-depth `PermissionError`. Миграция `020_creation_requires_evaluation.sql` закрепляет правило в RLS: `user_has_published_evaluation(uuid)` + insert в `achievements` только с оценкой опубликованного; EXECUTE только у `authenticated` (`revoke ... from public, anon`). Миграция проверена в rollback-транзакции и применена к проду (201); `anon_can_execute=false`, `authenticated_can_execute=true`. Тесты — **165 passed**, ruff чист. Тестовые аккаунты удалены, достижения #35/#36 сохранены за `lucasteam`. Запушено (`497eb00`, `b9317a3`), CI run #40 — success; прод проверен: каталог и карточки #35/#36 отдают `lucasteam`, тестовых подписей нет.

**2026-11-08 — VKUI-оформление и надёжная загрузка доказательств.** `app/static/css/style.css` переписан на токенах `@vkontakte/vkui` 8: светлая тема по умолчанию, тёмная через `prefers-color-scheme`, компоненты (шапка, hero, карточки, кнопки, поля, чипы, бейджи, таблицы, профиль, auth, flash, error-page, scale-picker). `base.html`: `color-scheme`, два `theme-color`, `style.css?v=2`. Вёрстка проверена через CDP (light/dark, desktop/mobile 390 px, без переполнений). Загрузка proof-файлов: `_storage_error_message` отдаёт понятные тексты вместо сырых ответов Storage (413/403/RLS/404), детали — в лог; `user_client_from_request` использует токены, обновлённые `resolve_user` в `request.state` (иначе 401 после refresh). Создание достижения требует хотя бы одной оценки. `pytest` — **165 passed**, ruff чист.

**2026-11-07 — v1.5: новые оценки меняют топ.** Снят lock: `submit_evaluation` и `apply_completion_evaluation` принимают оценки и после `ranked`, пересчитывают среднее/ранги/BT после каждой новой заявки. Каждый подтверждённый участник обязан указать сложность при отправке заявки; форма «Я выполнил это» снова доступна для ranked. Миграция 019 применена; live-проверка: `test_player` добавил 4-ю оценку к «Выпить стакан воды», достижение пересчиталось. Тесты — 153 passed.

**2026-11-07 — v1.4: сложность при создании/прохождении + сравнительный выбор.** При создании достижения спрашивается «Ожидаемая сложность», при прохождении — «Сложность»; вместо топ-1/2/3 используется бинарный виджет «сложнее/легче, чем …» (общий partial + JS). Выбор хранится в `achievement_completions.requested_harder_count`; после approve RPC `apply_completion_evaluation` (миграция 017) создаёт обычную оценку автора. Миграция 018: если у пользователя ещё нет оценок, первая approved-заявка автоматически становится якорем (позиция 1), включая backfill для существующих; supersonic пользователя получил position=1. Тесты — 152 passed.

**2026-11-06 — «Мои заявки» и переотправка отклонённых.** Добавлен `/submissions`: список своих заявок со статусами, доказательствами и причиной отклонения; фильтры по статусу; ссылка в навигации. В карточке достижения отклонённая заявка показывает причину и форму повторной отправки (старая заявка и файлы заменяются). `submit_completion` уже умел reset rejected через RPC — теперь это доступно из UI. Миграция 016 открывает автору `moderation_reviews` по его completion. `pytest` — 146 passed.

**2026-11-06 — v1.3: вход через LTHub (OAuth 2.0 + PKCE).** В BankBot добавлен OAuth-provider (`core/oauth.py`, коммит `f1746ed`): authorize-страница на базе `localStorage.web_token`, одноразовые коды (TTL 5 мин, SHA-256), `/oauth/token` с PKCE S256, `/oauth/userinfo`, metadata. В GAL — `GET /auth/lthub` + `/auth/lthub/callback` (`app/routers/lthub_auth.py`), привязка identity через `external_identities` (миграция 015, service_role), создание Supabase-сессии (`app/services/lthub_accounts.py`), кнопки на входе/регистрации. Username берётся из ника LTHub (fallback `lthub_<subject>`); старые `lthub_<subject>` подтягиваются при следующем входе. Прод-E2E: LTHub `shadowraven` → code → GAL callback → `gal_session`; тестовый GAL-пользователь удалён. `pytest` — 141 passed. Холодный старт LTHub ~57 с (Vercel-лимит 60 с), после прогрева authorize ~1.4 с.

**2026-11-06 — v1.2 (D13): Bradley–Terry внедрён и задеплоен.** Глобальный ранкинг переведён с средней позиции на силу BT. `app/services/bradley_terry.py` строит попарные сравнения из личных шкал (все пары, вес `1/(k-1)`) и оценивает силы регуляризованным MM (prior κ=0.5, центрирование). `app/services/bt_recompute.py` читает все оценки service role и применяет результат через RPC `apply_bt_ratings(jsonb)`. Миграция `014` (колонки `bt_*`, guard, ранжирование по BT, RPC) применена к проду (201) и проверена: функция переставляет ранги, anon получает `42501`, `service_role` — 0. UI проверен на проде временным достижением: лидерборд показал «BT-рейтинг 1217», карточка — силу 1.250 и 42 сравнения; тестовые данные удалены (`achievements=0`, `profiles=0`). Эксперимент: Spearman 0.9908, Kendall 0.9333, bootstrap Spearman 0.9910, top-10 overlap 0.93. Прод v1.2.0, CI зелёный, `pytest` — 135 passed.

**2026-11-06 — v1.1 (D12) завершён и проверен на проде.** CI зелёный на `7ed168d`: `ruff==0.17.4` закреплён, UP017/FURB162 исправлены, `pytest` — **123 passed**. Live-дефект 416 закрыт: каталог и оба лидерборда сначала запрашивают count, затем данные; `/achievements?page=2` и `/leaderboard?page=2` теперь 303 на валидную страницу вместо 503. Миграция `013` применена к проду (201) и проверена SQL. Прод: `/openapi.json` — v1.1.0, ключевые маршруты 200, 404 — HTML.

**2026-11-05 — v1.1 (D12).** Закрыты: HTML-страницы 404/403, реальный лендинг вместо заглушки, навигация по ролям, русская локализация и подписи enum/дат, редактирование/удаление своих `pending`-достижений, скрытие достижения модератором, история модерации, настройки профиля, смена пароля и завершение сброса пароля, пагинация/поиск каталога и лидербордов, адаптивная вёрстка и JS (меню, pre-check файла, confirm). Безопасность/целостность: удалена публичная RLS-политика proofs, добавлен deferred trigger «публикация только с approved proof создателя», исправлено удаление битой cookie (`kwargs` в middleware), `NOT_FOUND` из `submit_evaluation` маппится в понятное сообщение, SupabaseNotConfiguredError → 503, добавлена миграция `013`, CI (ruff + pytest).

**Инцидент 2026-11-05:** прод-проект Supabase `wiwyitafoprxmlyndkwe` снова был `INACTIVE` — все страницы с данными отдавали 503 (лендинг/логин работали). Восстановлен через `POST /v1/projects/{ref}/restore` (200 → COMING_UP → ACTIVE_HEALTHY); после восстановления `/`, `/achievements`, `/leaderboard`, `/auth/*`, `/docs` снова 200. Прод-БД пустая (тестовые данные удалены ранее).

## Данные на проде (2026-11-08)

- Единственный аккаунт: `lucasteam` (admin, вход через LTHub OAuth).
- Тестовые аккаунты `test.player.gal@example.com` и `test.eval1..3.gal@example.com` удалены 2026-11-08 вместе с их оценками, заявками и proofs.
- Достижения #35 «Выпить стакан воды» и #36 «Заснуть раньше полночи» сохранены и переназначены на `lucasteam` (решение пользователя); также остаются #31 (supersonic, published), #32 (rejected) и #33 (шортс, published).
- После очистки у `lucasteam` 3 оценки (#31, #33, #36), 3 approved-заявки с proofs, 6 записей истории модерации, 1 external identity. После снижения порога до 2 и backfill (2026-11-11): #46 — rank 1, #33 — rank 2, #35 — rank 3; лидерборд непустой, BT-метрики синхронизированы с `evaluations`.
- **Порог ранжирования:** 2 оценки (миграция 022 применена к проду 2026-11-11); backfill старых `unknown`-достижений с 2 оценками — миграция 024.
- **Публичные списки прошедших:** миграция 023 не применена к проду. До её применения карточки достижений и профили на проде показывают пустые списки (RPC отсутствует → ошибка ловится, страница не падает).

## Статус задач

- D13 (v1.2, Bradley–Terry): **completed** — код v1.2.0 задеплоен, миграция 014 применена и проверена, CI зелёный.
  - [x] попарные сравнения из личных шкал + веса
  - [x] регуляризованный MM, центрирование, rating-шкала
  - [x] `bt_*` колонки, guard, RPC `apply_bt_ratings`, rank по BT
  - [x] автопересчёт после оценок/модерации/жалоб + ручной скрипт
  - [x] UI: BT-рейтинг, сравнения, пояснение
  - [x] эксперимент точности/устойчивости и docs/bradley_terry.md
  - [x] тесты 135, ruff, CI; миграция и прод проверены
- D12 (v1.1): **completed**
  - [x] 404/403/503-страницы, лендинг, навигация, локализация
  - [x] edit/soft-delete pending, hide, история модерации
  - [x] профиль, смена пароля, `/auth/update-password`
  - [x] пагинация/поиск (count-first, 303 вместо 416/503)
  - [x] RLS proofs/история, инвариант proof создателя
  - [x] адаптивная вёрстка, JS, CI, docs
- D3 (Authentication): **completed** — включая LTHub OAuth (v1.3): PKCE, state, external_identities, автологин/создание Supabase-пользователя.
- D1–D11: **completed**

## Ключевые изменения (v1.3)

1. BankBot `core/oauth.py` — OAuth 2.0 provider: authorize/consent, one-time hashed codes, token + PKCE S256, userinfo, metadata; регистрация из `api/index.py`.
2. BankBot login page учитывает `?redirect=` (возврат в authorize-flow).
3. GAL `app/routers/lthub_auth.py` — state/PKCE cookies, обмен кода, userinfo, ошибки на логине.
4. GAL `app/services/lthub_accounts.py` — сервисный Supabase admin: create/update password, сессия, привязка `external_identities`.
5. `migrations/015_external_identities.sql` — таблица привязок, RLS + service_role only.
6. Login/register: кнопки «Войти через LTHub»; env-настройки LTHub.

## Ключевые изменения (v1.2)

1. `app/services/bradley_terry.py` — `build_comparisons`, `fit_bradley_terry` (MM), `win_probability`, `ordered_ids`.
2. `app/services/bt_recompute.py` — выборка evaluations/achievements service role, батчи по 1000, применение RPC.
3. `migrations/014_bradley_terry.sql` — `bt_*` колонки, guard, ранжирование по BT, `apply_bt_ratings(jsonb)`.
4. `app/services/{evaluation,moderation,reports}.py` — вызов best-effort пересчёта после изменений.
5. UI: BT-рейтинг/сила/сравнения в карточке, BT-колонки и пояснение в лидерборде.
6. `scripts/bt_experiment.py`, `scripts/recompute_bt.py`, `docs/bradley_terry.md`.
7. `app/services/ranking.py` — порядок по `bt_strength desc` с fallback на среднюю позицию.

## Ключевые изменения (v1.1)

1. `app/presentation.py` — фильтры подписей и дат, регистрация в `templating.py`.
2. `app/main.py` — обработчики `StarletteHTTPException` (403/404/…), `SupabaseNotConfiguredError` → 503, лендинг с best-effort топами.
3. `app/routers/achievements.py`, `app/services/achievements.py` — пагинация/поиск каталога, edit/soft-delete своих pending, фильтрация статусов профиля.
4. `app/routers/moderation.py`, `app/services/moderation.py` — hide, история, approve только после approving creator completion с proof, отклонение заявок при reject/hide.
5. `app/routers/settings.py`, `app/services/profiles.py` — профиль и смена пароля.
6. `app/routers/auth.py`, `templates/auth/update_password.html` — завершение сброса пароля через Supabase Auth из браузера.
7. `migrations/013_ui_features_and_hardening.sql` — RLS-политики, deferred trigger, `player_leaderboard_page`.
8. `app/middleware.py` — `kwargs` cookie (удаление битой сессии с `max-age=0`).
9. `.github/workflows/ci.yml`, обновлённые README/docs.

## Следующие шаги

- Прогнать `scripts/bt_experiment.py` на реальных данных после наполнения прода; сравнить BT с Plackett–Luce/средней позицией.
- Применить миграцию 025 и задеплоить v1.8 после отдельного подтверждения; проверить SE/CI на живых данных.
- Сравнить бутстрэп-SE с оценкой по гессиану/Plackett–Luce и решить, нужен ли переход на полные ранги.
- Наполнить прод реальными достижениями и проверить полный цикл с обычным пользователем и модератором.
- Проверить повторный вход через LTHub (переиспользование `external_identities`) после прогрева LThub; закрыть cold-start 57 с (оптимизация импорта/перенос OAuth в отдельную функцию).

## Риски

- **Локальная сеть блэкхолит TCP-диапазоны Supabase** (`8.47.69.0/24`, `8.6.112.0/24`): локальные live-e2e невозможны, проверка — через Management API и прод. Обход: VPN/прокси. Также локально нестабилен TLS до `*.vercel.app` — live-проверки делать через внешний fetch.
- **Порядок миграции 013 (уже соблюдён):** trigger блокирует публикацию без approved proof создателя. Новый код одобряет completion до публикации; старый код делал наоборот. При откате кода на версию < v1.1 возможны отказы публикации.
- **Сброс пароля** рассчитан на implicit-flow Supabase (токены в `#access_token`). Если в проекте включат PKCE, страницу нужно доработать под `?code=`.
- **Bradley–Terry vs fallback:** пока `apply_bt_ratings` не выполнился (нет service role/данных), ранги считаются по `average_position`; это осознанный fallback, а не ошибка. После миграции 014 на существующей БД нужно один раз запустить `scripts/recompute_bt.py`.
- **Стоимость пересчёта:** BT пересчитывается целиком после каждой оценки/решения (O(N·iter) + чтение всех evaluations). На малых данных нормально; при росте — батч/материализация и, возможно, инкрементальные обновления.
- **Зависимость наблюдений:** все пары одной шкалы зависимы; весовое нормирование `1/(k-1)` не заменяет Plackett–Luce. Для отчётности это надо явно указывать.
- **LTHub OAuth:** холодный старт BankBot-функции бывает ~57 с при лимите Vercel 60 с → authorize может отдать 504; после прогрева ~1.4 с. OAuth-коды/токены хранятся в БД LThub; при недоступности LThub вход через него не работает, парольный вход GAL остаётся.
- **Служебные GAL-аккаунты LTHub:** email `lthub-oauth-<sub>@example.com`, пароль ротируется при каждом входе; это OAuth-only аккаунты, сброс пароля по email для них бессмыслен. Удаление auth-пользователя каскадно чистит профиль и identity.
