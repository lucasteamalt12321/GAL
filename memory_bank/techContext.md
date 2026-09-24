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

## Зависимости (Plan)

```text
fastapi
uvicorn[standard]
jinja2
pydantic-settings
supabase
httpx
python-multipart
python-jose[cryptography]   # при необходимости верификации токенов
```

Тестовые: `pytest`, `httpx`.

## Доступ к БД (решение, D1)

- Пользовательские операции: клиент `supabase` (PostgREST) с anon key + user JWT — RLS применяется.
- Привилегированные серверные операции (модерация, пересчёт ранкинга) — service role.
- Транзакционный пересчёт ранкинга (§57): решить на D7 (Postgres RPC либо контролируемый серверный пересчёт).

## Переменные окружения

```text
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=   # только backend
SUPABASE_JWT_SECRET=         # при необходимости
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
  - `004_storage_proofs.sql` — bucket `proofs` (private, 50 МБ) + политики `storage.objects`.
- Storage bucket `proofs` — приватный; файлы по пути `{user_id}/{achievement_id}/{uuid}-{file}`; доступ по signed URL (TTL 3600), RLS на `storage.objects`.
- Миграции применяются через Management API: `POST https://api.supabase.com/v1/projects/{ref}/database/query` с `Authorization: Bearer <PAT>` (успех = 201), т.к. `psql`/supabase CLI недоступны.
- API supabase-py 2.31: auth = `supabase_auth.SyncGoTrueClient` (`SyncClient`/`SyncSupabaseAuthClient` — импорт падает); Storage = `client.storage.from_("proofs")` (`storage3._sync.file_api.SyncBucketProxy`, `upload(path, bytes, FileOptions)`, `create_signed_url(path, expires_in)` → dict с `signedURL`/`signedUrl`, `remove`, `list`); исключения `storage3.exceptions.StorageException`/`StorageApiError`.
- `FileOptions.upsert` передавать булевым (строка `"false"` трактуется storage3 как truthy → ставит `x-upsert`).
- `set_session(access, refresh)` эмитит `TOKEN_REFRESHED` → PostgREST клиента переключается на JWT пользователя, RLS применяется к `.table()`.

## Локальная разработка

- `uvicorn app.main:app --reload`
- Реальный Supabase development project.

## CI/CD (план)

- Push → Vercel (dev preview) → production.
- ruff на PR/commit.
- pytest для ranking engine минимум.

## Правила проверки кода

- Всегда прогонять `ruff check` + `ruff format` после изменений.
- Markdown-файлы линтером не проверяются.