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

### `app/services/`
| Модуль          | Назначение                                   |
|-----------------|----------------------------------------------|
| `ranking.py`    | Ядро: среднее, сортировка, tie-break, rank   |
| `scoring.py`    | `1000 / rank`, суммарные очки, вычисляемые  |
| `evaluation.py` | Personal scale, locking                      |
| `moderation.py` | Правила approve/reject                       |
| `achievements.py`| Жизненный цикл достижений                   |

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

Документация соответствует состоянию на момент завершения D1 (Foundation): каркас FastAPI, клиенты Supabase, миграция `001_init.sql`, health-эндпоинты. Обновлять при изменениях архитектуры, маршрутов и модулей.