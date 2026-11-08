# Active Context — GAL

## Текущий фокус

**2026-11-08 — VKUI-оформление и надёжная загрузка доказательств.** `app/static/css/style.css` переписан на токенах `@vkontakte/vkui` 8: светлая тема по умолчанию, тёмная через `prefers-color-scheme`, компоненты (шапка, hero, карточки, кнопки, поля, чипы, бейджи, таблицы, профиль, auth, flash, error-page, scale-picker). `base.html`: `color-scheme`, два `theme-color`, `style.css?v=2`. Вёрстка проверена через CDP (light/dark, desktop/mobile 390 px, без переполнений). Загрузка proof-файлов: `_storage_error_message` отдаёт понятные тексты вместо сырых ответов Storage (413/403/RLS/404), детали — в лог; `user_client_from_request` использует токены, обновлённые `resolve_user` в `request.state` (иначе 401 после refresh). Создание достижения требует хотя бы одной оценки. `pytest` — **160 passed**, ruff чист. Коммит и прод-проверка — в работе.

**2026-11-07 — v1.5: новые оценки меняют топ.** Снят lock: `submit_evaluation` и `apply_completion_evaluation` принимают оценки и после `ranked`, пересчитывают среднее/ранги/BT после каждой новой заявки. Каждый подтверждённый участник обязан указать сложность при отправке заявки; форма «Я выполнил это» снова доступна для ranked. Миграция 019 применена; live-проверка: `test_player` добавил 4-ю оценку к «Выпить стакан воды», достижение пересчиталось. Тесты — 153 passed.

**2026-11-07 — v1.4: сложность при создании/прохождении + сравнительный выбор.** При создании достижения спрашивается «Ожидаемая сложность», при прохождении — «Сложность»; вместо топ-1/2/3 используется бинарный виджет «сложнее/легче, чем …» (общий partial + JS). Выбор хранится в `achievement_completions.requested_harder_count`; после approve RPC `apply_completion_evaluation` (миграция 017) создаёт обычную оценку автора. Миграция 018: если у пользователя ещё нет оценок, первая approved-заявка автоматически становится якорем (позиция 1), включая backfill для существующих; supersonic пользователя получил position=1. Тесты — 152 passed.

**2026-11-06 — «Мои заявки» и переотправка отклонённых.** Добавлен `/submissions`: список своих заявок со статусами, доказательствами и причиной отклонения; фильтры по статусу; ссылка в навигации. В карточке достижения отклонённая заявка показывает причину и форму повторной отправки (старая заявка и файлы заменяются). `submit_completion` уже умел reset rejected через RPC — теперь это доступно из UI. Миграция 016 открывает автору `moderation_reviews` по его completion. `pytest` — 146 passed.

**2026-11-06 — v1.3: вход через LTHub (OAuth 2.0 + PKCE).** В BankBot добавлен OAuth-provider (`core/oauth.py`, коммит `f1746ed`): authorize-страница на базе `localStorage.web_token`, одноразовые коды (TTL 5 мин, SHA-256), `/oauth/token` с PKCE S256, `/oauth/userinfo`, metadata. В GAL — `GET /auth/lthub` + `/auth/lthub/callback` (`app/routers/lthub_auth.py`), привязка identity через `external_identities` (миграция 015, service_role), создание Supabase-сессии (`app/services/lthub_accounts.py`), кнопки на входе/регистрации. Username берётся из ника LTHub (fallback `lthub_<subject>`); старые `lthub_<subject>` подтягиваются при следующем входе. Прод-E2E: LTHub `shadowraven` → code → GAL callback → `gal_session`; тестовый GAL-пользователь удалён. `pytest` — 141 passed. Холодный старт LTHub ~57 с (Vercel-лимит 60 с), после прогрева authorize ~1.4 с.

**2026-11-06 — v1.2 (D13): Bradley–Terry внедрён и задеплоен.** Глобальный ранкинг переведён с средней позиции на силу BT. `app/services/bradley_terry.py` строит попарные сравнения из личных шкал (все пары, вес `1/(k-1)`) и оценивает силы регуляризованным MM (prior κ=0.5, центрирование). `app/services/bt_recompute.py` читает все оценки service role и применяет результат через RPC `apply_bt_ratings(jsonb)`. Миграция `014` (колонки `bt_*`, guard, ранжирование по BT, RPC) применена к проду (201) и проверена: функция переставляет ранги, anon получает `42501`, `service_role` — 0. UI проверен на проде временным достижением: лидерборд показал «BT-рейтинг 1217», карточка — силу 1.250 и 42 сравнения; тестовые данные удалены (`achievements=0`, `profiles=0`). Эксперимент: Spearman 0.9908, Kendall 0.9333, bootstrap Spearman 0.9910, top-10 overlap 0.93. Прод v1.2.0, CI зелёный, `pytest` — 135 passed.

**2026-11-06 — v1.1 (D12) завершён и проверен на проде.** CI зелёный на `7ed168d`: `ruff==0.17.4` закреплён, UP017/FURB162 исправлены, `pytest` — **123 passed**. Live-дефект 416 закрыт: каталог и оба лидерборда сначала запрашивают count, затем данные; `/achievements?page=2` и `/leaderboard?page=2` теперь 303 на валидную страницу вместо 503. Миграция `013` применена к проду (201) и проверена SQL. Прод: `/openapi.json` — v1.1.0, ключевые маршруты 200, 404 — HTML.

**2026-11-05 — v1.1 (D12).** Закрыты: HTML-страницы 404/403, реальный лендинг вместо заглушки, навигация по ролям, русская локализация и подписи enum/дат, редактирование/удаление своих `pending`-достижений, скрытие достижения модератором, история модерации, настройки профиля, смена пароля и завершение сброса пароля, пагинация/поиск каталога и лидербордов, адаптивная вёрстка и JS (меню, pre-check файла, confirm). Безопасность/целостность: удалена публичная RLS-политика proofs, добавлен deferred trigger «публикация только с approved proof создателя», исправлено удаление битой cookie (`kwargs` в middleware), `NOT_FOUND` из `submit_evaluation` маппится в понятное сообщение, SupabaseNotConfiguredError → 503, добавлена миграция `013`, CI (ruff + pytest).

**Инцидент 2026-11-05:** прод-проект Supabase `wiwyitafoprxmlyndkwe` снова был `INACTIVE` — все страницы с данными отдавали 503 (лендинг/логин работали). Восстановлен через `POST /v1/projects/{ref}/restore` (200 → COMING_UP → ACTIVE_HEALTHY); после восстановления `/`, `/achievements`, `/leaderboard`, `/auth/*`, `/docs` снова 200. Прод-БД пустая (тестовые данные удалены ранее).

## Тестовые данные на проде (2026-11-07)

- Аккаунт: `test.player.gal@example.com` / `TestPass123` (`test_player`, «Тестовый игрок»), роль `user`.
- Оценщики: `test.eval1.gal@example.com`, `test.eval2.gal@example.com`, `test.eval3.gal@example.com` (пароль тот же, не для входа в UI).
- Достижения: #35 «Выпить стакан воды» (rank #1, 4 оценки после live-теста, BT ~1174) и #36 «Заснуть раньше полночи» (rank #2, 3 оценки, BT 826), категория Other, proof-ссылки.
- Данные созданы SQL через Management API; при необходимости удаляются каскадно по auth.users.

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
- Добавить доверительные интервалы/SE для BT (bootstrap на батче или гессиан) и показывать надёжность рядом с `bt_comparisons`.
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
