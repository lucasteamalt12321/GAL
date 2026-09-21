# Active Context — GAL

## Текущий фокус

D1–D4 завершены: каркас FastAPI на Vercel (`https://gal-inky.vercel.app`), миграции `001`–`003` применены к Supabase `wiwyitafoprxmlyndkwe`, аутентификация и достижения (список/карточка/создание) + профили работают. Следующий блок — D5 (Proofs + Moderation): загрузка доказательств в Storage, очередь модерации, approve/reject завершений и достижений.

## Статус задач

- D4 (Achievements CRUD): **completed**
  - [x] `app/services/achievements.py` — список/карточка/профиль/создание, эмбеддинги `category`+`creator`
  - [x] `app/routers/achievements.py` — `GET /achievements` (фильтр/сортировка), `GET /achievements/{id}`, `GET/POST /achievements/create`
  - [x] `app/routers/users.py` — `GET /users/{username}`
  - [x] Шаблоны `achievements/list.html`, `detail.html`, `create.html`, `users/profile.html`; CSS
  - [x] `tests/test_achievements.py` (8 тестов, с моками сервисного слоя)
  - [x] E2E-проверка: авторизованный create → 303 → карточка (pending badge), anon не видит pending, публичный список пуст
- D3 (Authentication): **completed**
  - [x] cookie-сессия Supabase Auth, `resolve_user`, refresh, dependencies, middleware, login/register/logout/recover/me
  - [x] Тесты `tests/test_auth.py` (6); живая проверка
- D2 (Database Schema + RLS): **completed**
  - [x] `001_init.sql` + `002_rls.sql` + `003_fix_handle_new_user.sql`
  - [x] Проверено на проде: 8 таблиц, 9 категорий, RLS, триггеры активны
- D1 (Foundation + Memory Bank): **completed**
- D5 (Proofs + Moderation): **pending**

## Принятые архитектурные решения

1. **Frontend:** Jinja2 + минимальный JS (серверный рендеринг).
2. **Backend:** Python + FastAPI (monolith: один backend + одна БД).
3. **DB/Auth/Storage:** Supabase (PostgreSQL + Auth + Storage).
4. **Доступ к БД:** официальный клиент `supabase` (PostgREST): anon для публичного чтения, user JWT (`set_session`) для записи под RLS, service role только для привилегированных серверных операций.
5. **Deploy:** Vercel (ASGI `app.main:app`); PAT `SUPABASE_ACCESS_TOKEN` в `.env` (только Management API/CLI, приложение его не читает).
6. **Draft статус:** отсутствует — только `pending → published` (упрощение MVP).
7. **Creator Evaluation:** включено — создатель автоматически первый completer/evaluater своего достижения.
8. **Дубликаты:** разрешены вручную модератором (без автодетекта в MVP).
9. **Ranking Engine** — отдельный модуль `app/services/ranking.py`, покрытый тестами (D7).
10. **Player Score** — производное значение (`1000 / rank`), не хранится; пересчитывается.
11. **Tie-breaking v1.0:** при равном average порядок фиксируется случайно при разрешении конфликта (не каждый просмотр).
12. **Транзакционность пересчёта ранкинга (§57):** решить на D7 (Postgres-функция через `rpc()` либо контролируемый серверный пересчёт).
13. **RLS:** роль меняется только service role; оценка только с `approved` completion; locked-оценки не редактируются.
14. **Auth-сессия:** httpOnly-cookie `gal_session`/`gal_refresh` (SameSite=Lax, `secure` в проде, 30 дней); middleware резолвит пользователя, при истечении access-токена делает refresh и переписывает cookie.
15. **`handle_new_user` — security definer** (owner `postgres`): без этого вставка в `profiles` под RLS ломает регистрацию (исправлено миграцией 003).
16. **`maybe_single()` может вернуть `None`** (новый postgrest) — во всех сервисах предусмотрена проверка на `None` перед `.data`.
17. **Чтение vs запись:** публичные страницы — anon-клиент; запись и просмотр собственного pending — user-JWT клиент (`client_for_request`/`user_client_from_request`).

## Открытые вопросы (не блокируют MVP)

- `?` Score политика → MVP: 0 очков за `unknown` достижения.
- Первое достижение пользователя (нет personal scale) → оценка относительно глобальной шкалы как исключение.
- Вес оценок по опыту → будущие версии.
- **Email-конфирмация включена** в Supabase (при регистрации GoTrue отправляет письмо; замечен rate limit писем). Код обрабатывает оба случая: session есть → вход; нет → сообщение «подтвердите email».
- Уникальность `username`: только БД-индекс; коллизия → ошибка `handle_new_user` (обработать в D10).
- `/auth/recover` без `redirect_to`; письма ведут на дефолтный URL (настроить в D10).

## Следующие шаги

1. D5 — Proofs + Moderation: Storage bucket `proofs`; `app/routers/completions.py` (заявка на выполнение + доказательство), `app/routers/moderation.py` (очередь, approve/reject достижений и completions), `app/services/moderation.py`; шаблоны; тесты.

## Риски на горизонте

- Ранкинг чувствителен к tie-breaking и атомарности пересчёта → покрыть тестами раньше всего (D7).
- CSRF для форм: MVP — SameSite=Lax; полноценный CSRF-токен на D10.
- Storage-политики bucket `proofs` (upload/read) нужно настроить одновременно с bucket в D5.