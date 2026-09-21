# GAL — Architecture Overview

## Global Achievement List v1.0

Платформа для глобального рейтинга человеческих достижений из разных сфер через единую относительную шкалу сложности, построенную на личных сравнениях пользователей.

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

- **FastAPI** — вся бизнес-логика (ранкинг, scroing, модерация, оценки).
- **Supabase** — инфраструктура: PostgreSQL, авторизация, хранение доказательств.

## Модули

### `app/routers/`
| Модуль          | Назначение                                   |
|-----------------|----------------------------------------------|
| `auth.py`       | Регистрация, вход, выход, сессии             |
| `users.py`      | Профили, личные страницы                     |
| `achievements.py`| CRUD достижений                             |
| `completions.py`| Выполнения + доказательства                 |
| `evaluations.py`| Личная шкала и оценки                        |
| `rankings.py`   | Achievement / Player leaderboards            |
| `reports.py`    | Жалобы                                       |
| `moderation.py` | Очередь модерации, решения                   |

### `app/` (инфраструктура)
| Модуль             | Назначение                                                    |
|--------------------|---------------------------------------------------------------|
| `main.py`          | Сборка FastAPI, подключение роутеров, middleware, статики     |
| `config.py`        | Настройки (pydantic-settings), флаги `supabase_configured`    |
| `templating.py`    | Общий `Jinja2Templates`                                       |
| `middleware.py`    | `UserContextMiddleware` — кладёт текущего пользователя в `request.state` |
| `dependencies.py`  | `get_current_user` / `get_current_moderator` / `get_current_admin` |
| `database/`        | Ленивые клиенты Supabase (anon + service role)                |

### `app/services/`
| Модуль          | Назначение                                   |
|-----------------|----------------------------------------------|
| `auth.py`       | Cookie-сессия Supabase Auth, `resolve_user`, refresh токена |
| `ranking.py`    | Ядро: среднее, сортировка, tie-break, rank   |
| `scoring.py`    | `1000 / rank`, суммарные очки, вычисляемые  |
| `evaluation.py` | Personal scale, locking                      |
| `moderation.py` | Правила approve/reject                       |
| `achievements.py`| Жизненный цикл достижений                   |

## Аутентификация (D3)

- `POST /auth/register`, `POST /auth/login` — Supabase Auth; токены в httpOnly-cookie `gal_session` + `gal_refresh` (SameSite=Lax, `secure` в проде).
- `POST /auth/logout` — `sign_out` + удаление cookie.
- `POST /auth/recover` — письмо для сброса пароля.
- `GET /auth/me` — JSON текущего пользователя (401 без сессии).
- `UserContextMiddleware` резолвит пользователя на каждый запрос; при истёкшем access-токене делает refresh и обновляет cookie. `/static` и `/health` пропускаются.
- Профиль читается из `profiles` (роль, username, avatar).

## Данные

```text
profiles
categories
achievements
achievement_completions
proofs
evaluations
moderation_reviews
reports
```

**Производные значения** (не канон): `achievements.rank`, `achievements.average_position`, суммы очков игроков — пересчитываются из первичных данных.

## Главная цепочка

```text
Achievement → Creator Proof → Moderation → Published
   → User Completion → Completion Proof → Moderation
   → Personal Evaluation → Average Position → Global Rank
   → 1000 / Rank → Player Leaderboard
```

## Ключевые правила

- Достижение публикуется только после подтверждения собственного выполнения создателем.
- Оценка доступна только обладателю `approved` completion; одно достижение = одна оценка.
- Минимум 3 оценки для числовой позиции (иначе `?`).
- После появления числового рейтинга оценки фиксируются (`locked`).
- При равных средних порядок фиксируется случайно при разрешении конфликта.
- Очки игрока = `1000 / rank` каждого уникального подтверждённого достижения, пересчитываются автоматически.
- Достижения со статусом `unknown` (`?`) дают 0 очков в MVP.

## Жизненные циклы

- Achievement: `pending → published` / `rejected`; модератор → `deleted`.
- Completion: `pending → approved` / `rejected`.
- Evaluation: editable пока достижение `unknown`; `locked` после `ranked`.
- Report: `pending → accepted` / `rejected`.

## Развёртывание

- Vercel: `vercel.json`, ASGI = `app.main:app`.
- Переменные: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`.
- `SERVICE_ROLE_KEY` только на сервере.

## Статус

Документация соответствует состоянию на завершение D3: каркас FastAPI, клиенты Supabase, миграции `001_init.sql` + `002_rls.sql` (применены к проду), health-эндпоинты, аутентификация (cookie-сессия, login/register/logout/recover/me, `UserContextMiddleware`), деплой на Vercel. Обновлять при изменениях архитектуры, маршрутов и модулей.