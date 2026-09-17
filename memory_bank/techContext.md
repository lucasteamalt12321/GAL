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

## Supabase (новый проект — создать)

- Проект создаётся с нуля (пользователь: «Создать новый»).
- Миграции SQL в `migrations/`:
  - `001_init.sql` — таблицы;
  - `002_rls.sql` — RLS policies.
- Toggle: включить Storage bucket `proofs`.

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