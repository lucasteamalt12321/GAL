# Active Context — GAL

## Текущий фокус

D1 (Foundation) завершён. Каркас FastAPI + Supabase-клиенты + миграция `001_init.sql` + health-эндпоинты + тесты — готовы. Следующий блок — D2 (Schema + RLS) и D3 (Authentication), ожидает одобрения пользователя.

## Статус задач

- D1 (Foundation + Memory Bank): **completed**
  - [x] Каркас FastAPI: `main.py`, `config.py`, `database/connection.py`
  - [x] `routers/health.py`, homepage `/`, templates base/index, static CSS
  - [x] `requirements.txt`, `vercel.json`, `.env.example`, `.gitignore`, `README.md`
  - [x] `migrations/001_init.sql` (полная схема + триггеры + seed категорий)
  - [x] `tests/test_health.py` (4 passed), ruff clean, uvicorn стартует
  - [x] Memory Bank синхронизирован, пуш в GitHub

## Принятые архитектурные решения

1. **Frontend:** Jinja2 + минимальный JS (серверный рендеринг).
2. **Backend:** Python + FastAPI (monolith: один backend + одна БД).
3. **DB/Auth/Storage:** Supabase (PostgreSQL + Auth + Storage).
4. **Доступ к БД:** официальный клиент `supabase` (PostgREST): anon для пользовательских операций + user JWT, service role только для привилегированных серверных операций; RLS не обходится без необходимости.
5. **Deploy:** Vercel (ASGI `app.main:app`).
6. **Draft статус:** отсутствует — только `pending → published` (упрощение MVP).
7. **Creator Evaluation:** включено — создатель автоматически первый completer/evaluater своего достижения.
8. **Дубликаты:** разрешены вручную модератором (без автодетекта в MVP).
9. **Ranking Engine** — отдельный модуль `app/services/ranking.py`, покрытый тестами (D7).
10. **Player Score** — производное значение (`1000 / rank`), не хранится; пересчитывается.
11. **Tie-breaking v1.0:** при равном average порядок фиксируется случайно при разрешении конфликта (не каждый просмотр).
12. **Транзакционность пересчёта ранкинга (§57):** решить на D7 (Postgres-функция через `rpc()` либо контролируемый серверный пересчёт).

## Открытые вопросы (не блокируют MVP)

- `?` Score политика → MVP: 0 очков за `unknown` достижения.
- Первое достижение пользователя (нет personal scale для сравнения) → пока планируется разрешить оценку относительно глобальной шкалы как исключение.
- Вес оценок по опыту → будущие версии.
- Supabase-проект: создать пользователю; после этого применить миграции и подключить ключи.

## Следующие шаги

1. D2 — `migrations/002_rls.sql` (RLS-политики по §47).
2. D3 — Authentication: `routers/auth.py`, `services/auth.py`, cookie-сессия, `dependencies.py` (get_current_user / moderator / admin), шаблоны login/register.

## Риски на горизонте

- Ранкинг очень чувствителен к tie-breaking и атомарности пересчёта → покрыть тестами раньше всего (D7).
- RLS Supabase: критично правильно разделить права user/moderator (D2).
- Auth cookie-flow Supabase + FastAPI: требует аккуратной настройки (D3).