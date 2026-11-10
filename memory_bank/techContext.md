# Tech Context — GAL

## Стек

| Компонент    | Технология                          |
|--------------|-------------------------------------|
| Backend      | Python 3.12+, FastAPI               |
| Frontend     | Jinja2 (SSR) + минимальный JS/CSS   |
| База данных  | Supabase PostgreSQL                 |
| Auth         | Supabase Auth (email + password)    |
| Storage      | Supabase Storage (proofs)           |
| Деплой       | Vercel                              |
| Линтер       | ruff                                |
| Пакеты       | pip (requirements.txt)              |

## Зависимости

```text
fastapi
uvicorn[standard]
jinja2
pydantic-settings
supabase
httpx
python-multipart
```

Тестовые: `pytest`, `ruff==0.17.4`. Bradley–Terry реализован на чистом Python
(без numpy/scipy): `app/services/bradley_terry.py`.

## Доступ к БД (решение, D1)

- Пользовательские операции: клиент `supabase` (PostgREST) с anon key + user JWT — RLS применяется.
- Привилегированные серверные операции (модерация, BT-пересчёт) — service role.
- Пересчёт рангов транзакционный: SQL-функции `submit_evaluation` / `apply_bt_ratings`.

## Переменные окружения

```text
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=   # только backend
SUPABASE_JWT_SECRET=         # при необходимости
SUPABASE_ACCESS_TOKEN=       # Management API: миграции/e2e
APP_ENV=production           # CSRF + Secure cookies
APP_URL=                     # ссылка сброса пароля
SUPABASE_TIMEOUT_SECONDS=6   # таймаут PostgREST
DISPLAY_UTC_OFFSET_HOURS=3   # даты в UI
LTHUB_OAUTH_ENABLED=true     # кнопка «Войти через LTHub»
LTHUB_CLIENT_ID=gal          # публичный PKCE-клиент
LTHUB_AUTHORIZE_URL=https://lthub.vercel.app/oauth/authorize
LTHUB_TOKEN_URL=https://lthub.vercel.app/oauth/token
LTHUB_USERINFO_URL=https://lthub.vercel.app/oauth/userinfo
LTHUB_REDIRECT_URI=           # пусто = APP_URL + /auth/lthub/callback
OAUTH_PROVIDER_ENABLED=true   # GAL как OAuth-провайдер для LTHub
OAUTH_LTHUB_CLIENT_ID=lthub   # публичный PKCE-клиент LTHub
OAUTH_LTHUB_REDIRECT_URIS=    # доп. redirect URI через запятую (прод/localhost уже по умолчанию)
```

`.env.example` содержит шаблон; `.env` не коммитится.

## Vercel

- `vercel.json` — routing ASGI на `app.main:app`.
- Serverless функции; статистика не хранится локально.
- Переменные окружения задаются в Vercel dashboard.

## Supabase (проект создан: `wiwyitafoprxmlyndkwe`)

- Миграции SQL в `migrations/`:
  - `001_init.sql` — таблицы;
  - `002_rls.sql` — RLS policies;
  - `003_fix_handle_new_user.sql` — `handle_new_user` → `security definer`;
  - `004_storage_proofs.sql` — bucket `proofs` (private, 50 МБ) + политики `storage.objects`;
  - `005`–`012` — движок оценок, хардинг, integrity, lock, revoke internal execute;
  - `013_ui_features_and_hardening.sql` — soft-delete своих pending, приватность proofs, авторство истории модерации, deferred trigger «публикация только с approved proof создателя», постраничный `player_leaderboard_page`;
  - `014_bradley_terry.sql` — `bt_strength/bt_rating/bt_comparisons`, guard, ранжирование по BT, RPC `apply_bt_ratings(jsonb)` для `service_role`;
  - `015_external_identities.sql` — привязка LTHub identity к профилю (RLS, только `service_role`);
  - `016_completion_review_visibility.sql` — автор видит причину отклонения заявки;
  - `017_completion_difficulty.sql` — `requested_harder_count` + RPC `apply_completion_evaluation`;
  - `018_auto_first_evaluation.sql` — авто-якорь первой approved-заявки + backfill;
  - `019_live_evaluations.sql` — снятие lock, новые оценки после ranked, пересчёт топ;
  - `020_creation_requires_evaluation.sql` — RLS-запрет создания достижения без оценки опубликованного;
  - `021_oauth_provider.sql` — GAL как OAuth 2.0 provider (коды, токены, PKCE; клиенты — статический реестр в коде, таблицы `oauth_clients` нет; только `service_role`);
  - `022_restore_ranking_and_proof_scheme.sql` — канонический пересчёт рангов (BT + двухфазный bump), порог 2, защита ссылок-доказательств (`proofs_link_scheme`). **К проду не применена.**
- Storage bucket `proofs` — приватный; файлы по пути `{user_id}/{achievement_id}/{uuid}-{file}`; доступ по signed URL (TTL 3600), RLS на `storage.objects`.
- Миграции применяются через Management API: `POST https://api.supabase.com/v1/projects/{ref}/database/query` с `Authorization: Bearer <PAT>` (успех = 201), т.к. `psql`/supabase CLI недоступны.
- API supabase-py 2.31: auth = `supabase_auth.SyncGoTrueClient` (`SyncClient`/`SyncSupabaseAuthClient` — импорт падает); Storage = `client.storage.from_("proofs")` (`storage3._sync.file_api.SyncBucketProxy`, `upload(path, bytes, FileOptions)`, `create_signed_url(path, expires_in)` → dict с `signedURL`/`signedUrl`, `remove`, `list`); исключения `storage3.exceptions.StorageException`/`StorageApiError`.
- `FileOptions.upsert` передавать булевым (строка `"false"` трактуется storage3 как truthy → ставит `x-upsert`).
- `set_session(access, refresh)` эмитит `TOKEN_REFRESHED` → PostgREST клиента переключается на JWT пользователя, RLS применяется к `.table()`.

## Локальная разработка

- `uvicorn app.main:app --reload`
- Реальный Supabase development project.
- **Сеть (2026-10-20):** локальная сеть периодически блэкхолит TCP-диапазоны Supabase (`8.47.69.0/24`, `8.6.112.0/24`); DNS исправен (адреса пула ротируются), обрыв — именно на TCP-коннекте, не на резолве. Обход: VPN/прокси (для httpx — `HTTPS_PROXY` в окружении процесса) либо локальный Supabase (`supabase start`, нужен Docker). При недоступности Supabase БД-страницы отдают 503 (таймаут PostgREST 6 с), а не 500.

## CI/CD

- GitHub Actions: `.github/workflows/ci.yml` — `ruff check`, `ruff format --check`, `pytest` на push в `main` и PR.
- Push → Vercel (production).
- Локальная проверка: `pytest`, `ruff check .`, `ruff format --check .` (конфигурация — `ruff.toml`, `target-version = "py312"`, одноразовые скрипты исключены).
- Тесты: 196 (ранкинг/BT, OAuth клиент и провайдер, «Мои заявки», сложность/сравнения, живые оценки, роуты, CSRF, resilience, hardening, v1.1–v1.6 сценарии).
- Bradley–Terry: `scripts/recompute_bt.py` (service role), эксперимент `scripts/bt_experiment.py`; формула/ограничения — `docs/bradley_terry.md`.
- LTHub OAuth: публичный PKCE-клиент `gal`, provider в BankBot `core/oauth.py`; GAL-клиент — `app/routers/lthub_auth.py` + `app/services/lthub_accounts.py`.
- GAL как OAuth-провайдер: `app/services/oauth_provider.py` + `app/routers/oauth.py`; клиент LTHub (`lthub`, публичный, PKCE S256) — статический реестр; LTHub-клиент — `core/gal_oauth.py` (BankBot).
- `ruff` закреплён версией `0.17.4` в `requirements-dev.txt` (CI и локальная проверка совпадают).

## Правила проверки кода

- Всегда прогонять `ruff check` + `ruff format` после изменений.
- Markdown-файлы линтером не проверяются.