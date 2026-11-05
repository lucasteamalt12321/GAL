# GAL — Global Achievement List

Глобальная платформа рейтинга человеческих достижений. Достижения из разных сфер сравниваются через единую относительную шкалу сложности, построенную на личных сравнениях пользователей.

## Что умеет

- регистрация, вход, выход, восстановление пароля и смена пароля;
- предложение достижения с доказательством собственного выполнения (файл или ссылка);
- редактирование и удаление своего достижения, пока оно на модерации;
- модерация достижений и proofs, скрытие опубликованных достижений, история решений;
- личная шкала сложности: оценка доступна только после подтверждённого выполнения;
- глобальный рейтинг достижений (`1000 / rank`), очки игроков и два лидерборда;
- жалобы на достижения и их рассмотрение;
- профили, настройки профиля, пагинация и поиск по каталогу.

## Стек

- **Backend:** Python 3.12, FastAPI, Jinja2
- **Database / Auth / Storage:** Supabase (PostgreSQL + Auth + Storage)
- **Frontend:** Jinja2 SSR + небольшой vanilla JS/CSS
- **Deploy:** Vercel
- **Качество:** pytest, ruff, GitHub Actions

## Структура

```text
app/
├── main.py                # FastAPI app, обработчики ошибок, лендинг
├── config.py              # настройки из окружения (pydantic-settings)
├── presentation.py        # подписи enum-значений и форматирование дат
├── database/connection.py # клиенты Supabase (anon + service role)
├── routers/               # HTTP-маршруты
├── services/              # бизнес-логика
├── schemas/               # зарезервировано под Pydantic-схемы API
├── templates/             # Jinja2-шаблоны
└── static/                # CSS/JS/favicon

migrations/                # SQL-миграции Supabase (001–013)
scripts/                   # применение миграций и live-e2e
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

pip install -r requirements-dev.txt   # включает прод-зависимости и pytest/ruff

cp .env.example .env
```

Заполните `.env`:

| Переменная | Назначение |
|---|---|
| `SUPABASE_URL` | URL проекта Supabase |
| `SUPABASE_ANON_KEY` | публичный anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | server-only ключ для привилегированных операций |
| `SUPABASE_JWT_SECRET` | необязательно, для внешней верификации JWT |
| `SUPABASE_ACCESS_TOKEN` | Management API PAT — нужен `scripts/apply_migrations.py` |
| `APP_ENV` | `production` включает CSRF и `Secure`-cookies; по умолчанию `development` |
| `APP_URL` | публичный URL (для ссылки сброса пароля); по умолчанию берётся из запроса |
| `SUPABASE_TIMEOUT_SECONDS` | таймаут PostgREST, по умолчанию `6` |
| `DISPLAY_UTC_OFFSET_HOURS` | смещение дат в UI, по умолчанию `3` |

Без ключей приложение запустится, но страницы с данными вернут `503`, а `/health` покажет `supabase.configured: false`.

Запуск:

```bash
uvicorn app.main:app --reload
```

- `http://127.0.0.1:8000/` — главная
- `http://127.0.0.1:8000/health` — health-check
- `http://127.0.0.1:8000/docs` — OpenAPI

## Тесты и линтер

```bash
pytest
ruff check .
ruff format --check .
```

CI (`.github/workflows/ci.yml`) запускает ruff и pytest на каждый push в `main` и каждый pull request.

## База данных

Миграции лежат в `migrations/` и применяются **по порядку**:

- `001_init.sql` — схема, enum-типы, категории, триггеры;
- `002_rls.sql` — Row Level Security, гранты, helper-функции;
- `003_fix_handle_new_user.sql` — `handle_new_user` как `security definer`;
- `004_storage_proofs.sql` — приватный bucket `proofs` и политики Storage;
- `005_evaluation_rpc.sql` — движок оценок;
- `006_fix_evaluation_rpc.sql` — фикс неоднозначности OUT-параметров;
- `007_player_leaderboard.sql` — агрегат очков игроков;
- `008_hardening.sql` — хардинг RPC и служебных колонок;
- `009_retry_completion_rpc.sql` — повтор/очистка отклонённой заявки;
- `010_ranking_integrity.sql` — гранты, guard-триггер, внутренний пересчёт рангов;
- `011_evaluation_lock.sql` — блокировка оценок после перехода в `ranked`;
- `012_revoke_internal_execute.sql` — запрет вызова внутренней функции пересчёта;
- `013_ui_features_and_hardening.sql` — soft-delete своих pending, приватность proofs, история модерации, инвариант proof создателя, постраничный лидерборд.

Применение:

```bash
python scripts/apply_migrations.py            # все миграции по порядку
python scripts/apply_migrations.py 013        # только 013 (префикс номера)
python scripts/apply_migrations.py migrations/013_ui_features_and_hardening.sql
```

Скрипт использует Supabase Management API (`SUPABASE_ACCESS_TOKEN`) и последовательно выполняет SQL. Повторное применение `001` на существующей БД ожидаемо упадёт на `create type` — для существующего проекта применяйте только новые файлы.

## Деплой на Vercel

1. Импортируйте репозиторий в Vercel.
2. Задайте переменные окружения из таблицы выше.
3. Конфигурация уже в `vercel.json` (ASGI-приложение `app.main:app`).

`SUPABASE_SERVICE_ROLE_KEY` и `SUPABASE_ACCESS_TOKEN` никогда не должны попадать в клиентский код.

## Прод

1. Регистрация → создание достижения → proof → модерация → публикация.
2. Выполнение → proof → модерация → оценка → глобальный рейтинг → очки → player leaderboard.
3. Жалоба → очередь модератора → принятие (скрытие достижения) или отклонение.
