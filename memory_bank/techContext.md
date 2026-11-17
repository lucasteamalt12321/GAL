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
BT_BOOTSTRAP_REPLICATES=200  # бутстрэп SE/CI; 0 отключает
BT_BOOTSTRAP_SEED=20260101   # детерминизм бутстрэпа
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
  - `025_bt_uncertainty.sql` — `bt_rating_se/bt_rating_ci_low/bt_rating_ci_high`, guard новых полей, расширенный `apply_bt_ratings(jsonb)`. **Применена к проду 2026-11-14** (HTTP 201); значения появятся после пересчёта BT;
  - `015_external_identities.sql` — привязка LTHub identity к профилю (RLS, только `service_role`);
  - `016_completion_review_visibility.sql` — автор видит причину отклонения заявки;
  - `017_completion_difficulty.sql` — `requested_harder_count` + RPC `apply_completion_evaluation`;
  - `018_auto_first_evaluation.sql` — авто-якорь первой approved-заявки + backfill;
  - `019_live_evaluations.sql` — снятие lock, новые оценки после ranked, пересчёт топ;
  - `020_creation_requires_evaluation.sql` — RLS-запрет создания достижения без оценки опубликованного;
  - `021_oauth_provider.sql` — GAL как OAuth 2.0 provider (коды, токены, PKCE; клиенты — статический реестр в коде, таблицы `oauth_clients` нет; только `service_role`);
  - `022_restore_ranking_and_proof_scheme.sql` — канонический пересчёт рангов (BT + двухфазный bump), порог 2, защита ссылок-доказательств (`proofs_link_scheme`). **Применена к проду 2026-11-11** (HTTP 201): функции `submit_evaluation`/`apply_completion_evaluation` теперь `>= 2`, CHECK ссылок `NOT VALID` (нарушающих строк 0).
  - `023_public_completions.sql` — публичные RPC `achievement_completers(bigint)` и `user_completed_achievements(uuid)` для списков прошедших (approved + published, только поля профиля). **Применена к проду 2026-11-11** (HTTP 201), проверена на живых данных.
  - `024_backfill_ranked_threshold.sql` — backfill достижений с 2 оценками, оставшихся `unknown` до снижения порога; промоут в `ranked` с разными `rank` (защита уникального индекса) и `recompute_ranks_internal()`. **Применена к проду 2026-11-11** (HTTP 201).
  - `026_public_completion_proofs.sql` — RPC `achievement_completers`/`user_completed_achievements` дополнительно возвращают `proofs` (id, тип, описание, storage_path, дата) для approved-прохождений опубликованных достижений. **Применена к проду 2026-11-14** (HTTP 201).
  - `027_top5_temporary_rating.sql` — предварительный топ-5: таблицы `top5_applications`, `top5_evaluations`, `top5_expert_requests`, `top5_events`, `achievement_temporary_ratings`, enum-типы, RLS, guard/триггеры (независимость голосов, запрет самоодобрения, один голос эксперта), RPC заявок/решений и `apply_temporary_ratings(jsonb)` (service role), вьюха `public.public_achievements` с `permanent_rank`/`display_rank`/`is_temporary`/`in_leaderboard`. **Применена к проду 2026-11-14** (HTTP 201) после проверки в `BEGIN/ROLLBACK`; live-проверка в откатываемой транзакции подтвердила временную позицию `#1*` и `permanent_rank = null` для одобренной заявки. С 2026-11-17 приложение не использует экспертные приглашения: место в топ-5 выставляется вручную модератором, таблицы `top5_expert_requests` оставлены для совместимости.
  - `028_proof_visibility.sql` — `achievement_completions.proofs_visible boolean not null default true`, триггер `achievement_completions_creator_visibility` (верификация создателя всегда видима) и обновлённые `achievement_completers`/`user_completed_achievements`, которые отдают proofs только при `proofs_visible` либо владельцу/модератору. **Применена к проду 2026-11-15** (HTTP 201); все существующие прохождения остались видимыми (`default true`). Код сохраняет обратную совместимость при откате к версии без колонки.
  - `029_submit_evaluation_position_clamp.sql` — `submit_evaluation` зажимает позицию по `count(*)` других оценок пользователя вместо `max(position)` (нет «дыры» при редактировании не-последней оценки); инварианты 022 сохранены. **Применена к проду 2026-11-17** (валидация `BEGIN/ROLLBACK`, HTTP 201, postflight чистый).
  - `030_top5_apply_upsert.sql` — `apply_temporary_ratings` делает адресное удаление не-eligible строк + `on conflict (achievement_id) do update` вместо очистки всего кэша (stale payload не стирает свежие строки одобренных заявок); advisory lock и service-role-check сохранены. **Применена к проду 2026-11-17** (валидация `BEGIN/ROLLBACK`, HTTP 201, postflight чистый).
  - `031_top5_completion_claims.sql` — заявка в топ-5 вместе с прохождением (D18): `top5_applications.completion_id` (FK `on delete set null`) + partial index, `evidence_url` nullable (CHECK null/пусто/http(s)), guard INSERT для автора ИЛИ владельца прохождения, обновлённая RLS-insert политика, backfill старых активных заявок по approved-прохождению автора. **Применена к проду 2026-11-17** (валидация `BEGIN/ROLLBACK`, HTTP 201, postflight чистый); live-проверка в откатываемой транзакции: заявка от владельца прохождения (не автора) создаётся со статусом `pending`, без `completion_id` от не-автора — `NOT_AUTHOR`. Код сохраняет мягкую деградацию по `PGRST204` (`_is_missing_completion_column`) на случай отката схемы.
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
- Тесты: 296 (ранкинг/BT с инвариантом «каждая пара = соревнование», детерминизм, краевые случаи бутстрэпа и anchored strength, канонические определения SQL-миграций, guard/запись SE и CI, независимость и уникальность голосов топ-5, переход на третьей оценке, вьюха и fallback, видимость доказательств и запрет скрытия верификации создателя, отказоустойчивость входа по нику, OAuth клиент и провайдер, «Мои заявки», сложность/сравнения, живые оценки, роуты, CSRF, resilience, hardening, публичные профили/списки прошедших, заявки топ-5, v1.1–v1.10 сценарии).
- Bradley–Terry: `scripts/recompute_bt.py` (service role), эксперимент `scripts/bt_experiment.py`; SE/CI — кластерный бутстрэп (`BT_BOOTSTRAP_REPLICATES`, `BT_BOOTSTRAP_SEED`); временный топ-5 — `recompute_temporary_ratings` + `apply_temporary_ratings`; формула/ограничения — `docs/bradley_terry.md`.
- LTHub OAuth: публичный PKCE-клиент `gal`, provider в BankBot `core/oauth.py`; GAL-клиент — `app/routers/lthub_auth.py` + `app/services/lthub_accounts.py`.
- GAL как OAuth-провайдер: `app/services/oauth_provider.py` + `app/routers/oauth.py`; клиент LTHub (`lthub`, публичный, PKCE S256) — статический реестр; LTHub-клиент — `core/gal_oauth.py` (BankBot).
- `ruff` закреплён версией `0.17.4` в `requirements-dev.txt` (CI и локальная проверка совпадают).

## Правила проверки кода

- Всегда прогонять `ruff check` + `ruff format` после изменений.
- Markdown-файлы линтером не проверяются.