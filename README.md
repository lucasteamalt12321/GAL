# GAL — Global Achievement List

Глобальная платформа рейтинга человеческих достижений. Достижения из разных сфер сравниваются через единую относительную шкалу сложности, построенную на личных сравнениях пользователей.

## Стек

- **Backend:** Python 3.12, FastAPI, Jinja2
- **Database / Auth / Storage:** Supabase (PostgreSQL + Auth + Storage)
- **Frontend:** Jinja2 SSR + vanilla JS/CSS
- **Deploy:** Vercel

## Структура

```text
app/
├── main.py                # FastAPI app, шаблоны, статика
├── config.py              # настройки из окружения (pydantic-settings)
├── database/connection.py # клиенты Supabase (anon + service role)
├── routers/               # HTTP-маршруты
├── services/              # бизнес-логика
├── schemas/               # Pydantic-схемы
├── templates/             # Jinja2-шаблоны
└── static/                # CSS/JS

migrations/                # SQL-миграции Supabase
tests/                     # pytest
```

## Локальная разработка

Требуется Python 3.12+.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
```

Заполните `.env` ключами Supabase-проекта:

```text
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=   # только для серверной логики
SUPABASE_JWT_SECRET=
```

Без ключей приложение запустится, но `/health` вернёт `supabase.configured: false`.

Запуск:

```bash
uvicorn app.main:app --reload
```

Доступ:

- `http://127.0.0.1:8000/` — главная страница
- `http://127.0.0.1:8000/health` — health-check

## Тесты

```bash
pytest
```

## Линтер

```bash
ruff check .
ruff format .
```

## База данных

Миграции лежат в `migrations/`:

- `001_init.sql` — схема (таблицы, ограничения, триггеры);
- `002_rls.sql` — Row Level Security (добавляется на фазе D2).

Применяются через Supabase Dashboard → SQL Editor или cli: `supabase db push`.

## Деплой на Vercel

1. Импортируйте репозиторий в Vercel.
2. Задайте те же переменные окружения в dashboard.
3. Конфигурация: `vercel.json` (ASGI-приложение `app.main:app`).

`SERVICE_ROLE_KEY` никогда не должен попадать в клиентский код.

## Прод

1.  Регистрация → создание достижения → proof → модерация → публикация.
2.  Выполнение → proof → модерация → оценка → глобальный рейтинг → очки → player leaderboard.