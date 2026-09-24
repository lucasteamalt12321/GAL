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

## Достижения и профили (D4)

- `GET /achievements` — публичный список опубликованных достижений; фильтры `?category=<slug>`, `?sort=rank|new`.
- `GET /achievements/{id}` — карточка достижения (публичная; автор видит свои `pending`).
- `GET/POST /achievements/create` — форма и создание достижения (`pending`); требуется вход.
- `GET /users/{username}` — профиль: опубликованные достижения + свои `pending`.
- Чтение публичных данных — anon-клиент; для записей и просмотра собственного используется user-JWT клиент (`set_session` → PostgREST переключается на JWT пользователя, RLS применяется).
- `app/services/achievements.py` — доступ к данным; эмбеддинги `category` и `creator` (профиль) в одном запросе.
- Идентификатор категории для фильтра резолвится отдельным запросом по `slug` (надёжнее фильтра по embedded-ресурсу).

## Доказательства и модерация (D5)

- `POST /achievements/{id}/complete` — подача заявки о выполнении: создание `achievement_completions` (`pending`) + один proof (файл ≤50 МБ или ссылка). При создании достижения создатель обязан приложить proof своего выполнения.
- Proof файловый: загрузка в приватный bucket `proofs` по пути `{user_id}/{achievement_id}/{uuid}-{filename}`, затем строка в `proofs`. Proof-ссылка: строка в `proofs` со `proof_type=link` и URL в `storage_path`.
- `GET /moderation` — очередь `pending` достижений и заявок (только модератор/админ; иначе 403, аноним → login).
- `POST /moderation/achievements/{id}` и `POST /moderation/completions/{id}` — решение `approved`/`rejected`; решение фиксируется с `moderator_id` и `reason`.
- При approve достижения completion создателя помечается `approved` автоматически (без новой модерации).
- Просмотр файловых доказательств через signed URL (TTL 1 час); бакет не публичный. RLS `storage.objects`: владелец по первой части пути `(storage.foldername(name))[1] = auth.uid()`, модератор — через `public.is_moderator()`.
- `app/services/completions.py` — submit/attach/декор proofs; `app/services/moderation.py` — очередь и решения; `app/routers/completions.py`, `app/routers/moderation.py`.

## Миграции

- `001_init.sql` — схема и seed категорий.
- `002_rls.sql` — RLS-политики, grants, helper-функции.
- `003_fix_handle_new_user.sql` — `handle_new_user` помечен `security definer` (иначе вставка в `profiles` под RLS ломает регистрацию).
- `004_storage_proofs.sql` — bucket `proofs` (private, лимит 50 МБ) + политики `storage.objects` (insert/delete владельца, select владельца и модератора).

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

Документация соответствует состоянию на завершение D5: каркас FastAPI, клиенты Supabase, миграции `001`–`004` (применены к проду), аутентификация (cookie-сессия), достижения (список/карточка/создание), профили, доказательства выполнения (файлы/ссылки в Storage) и модерация (очередь и решения). Обновлять при изменениях архитектуры, маршрутов и модулей.