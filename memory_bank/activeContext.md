# Active Context — GAL

## Текущий фокус

D1–D7 завершены: каркас FastAPI на Vercel (`https://gal-inky.vercel.app`), миграции `001`–`006` применены к Supabase `wiwyitafoprxmlyndkwe`, аутентификация, достижения + профили, доказательства + модерация, движок оценок (личная шкала, `locked`, переход в `ranked`, `recompute_ranks()`) и чистый ранк-движок (`app/services/ranking.py`, `compute_ranks`/`player_score`). Следующие блоки — D8 (Player Score + Leaderboard) → D9 (Reports) → D10 (Polish).

## Статус задач

- D7 (Ranking Engine): **completed**
  - [x] `app/services/ranking.py`: `eligible_for_ranking` / `compute_ranks` / `player_score` (чистые функции)
  - [x] `tests/test_ranking.py` (14 тестов) — итого 53 passed, ruff чист
  - [x] Транзакционность ранкинга решена: пересчёт атомарен внутри `submit_evaluation`
- D6 (Evaluation Engine + Creator Eval): **completed**
  - [x] `migrations/005_evaluation_rpc.sql` + `006_fix_evaluation_rpc.sql` (баг 42702: `#variable_conflict use_column`) — применены к проду
  - [x] `app/services/evaluation.py`, `app/routers/evaluations.py`, блок оценки на карточке, `evaluations/evaluate.html`
  - [x] `tests/test_evaluations.py` (10) — итого 39 passed, ruff чист
  - [x] **Live-проверка на проде (SQL-канал): 14/14** — см. Changelog; critical bug 42702 найден и исправлен
- D5 (Proofs + Moderation): **completed**
  - [x] `migrations/004_storage_proofs.sql` — bucket `proofs` (private, 50 МБ) + политики `storage.objects` (insert/delete владельца по `foldername(name)[1]`, select владельца/модератора) — применена к проду (bucket+4 политики подтверждены)
  - [x] `app/services/completions.py` — `submit_completion`, `attach_proof` (файл ≤50МБ в `{uid}/{achievement_id}/{uuid}-{file}` или link), `proof_view` (signed URL TTL 3600), ошибки `CompletionError`/`AlreadyCompletedError`/`ProofError`
  - [x] `app/services/moderation.py` — очереди pending, `decide_achievement`/`decide_completion` (+ `moderation_reviews`), авто-approve completion создателя при approve достижения
  - [x] `app/routers/completions.py` (`POST /achievements/{id}/complete`), `app/routers/moderation.py` (`GET /moderation`, POST-решения; 403 для не-модератора, аноним → login)
  - [x] Create-флоу: proof создателя обязателен; `achievements/detail.html` — форма подачи, статус заявки, список proofs; `moderation/queue.html`; ссылка «Moderation» в nav для модераторов
  - [x] `tests/test_moderation.py` (11 тестов) — итого 29 passed, ruff чист
  - [x] Live E2E: create с link-proof → pending, очередь, submit на pending → 404, approve → published + creator completion auto-approved + review; user submit на published → 303. (Файловая часть E2E оборвалась из-за нестабильности прокси/сети; см. риски.)
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
18. **Proofs (D5):** bucket `proofs` приватный; путь `{uid}/{achievement_id}/{uuid}-{file}`; файловый proof читается только через signed URL (TTL 3600); `decorate_proofs`/`proof_view` тихо не ломаются при сетевых ошибках (возвращают proof без URL). Настройка `upsert` на Storage-upload передаётся булевым (строка `"false"` трактуется storage3 как truthy).
19. **Модерация (D5):** решение фиксируется в `moderation_reviews` с `moderator_id`/`reason`; при approve достижения completion создателя автоматически `approved`; `proof_type` для `text/plain` и пр. → `other`.
20. **Оценки (D6):** якорь первой оценки (нет личной шкалы) = позиция 1; оценка создателя — ручная, на странице (не блокирует публикацию); lock всех оценок сразу при переходе `unknown→ranked` (в D6); rank присваивается в момент перехода (`max(rank)+1`) + `recompute_ranks()`; tie-breaker `rank_order` фиксируется в этот же момент.
21. **Реализация оценок в БД (D6):** вся атомарная логика в security definer функции `submit_evaluation` (валидация + shift позиций + агрегаты + переход + lock). `#variable_conflict use_column` обязателен (RETURNS TABLE создаёт OUT-переменные, конфликтующие с колонками в UPDATE/ON CONFLICT — баг 42702).
22. **Live-проверки D6 (D6):** из-за нестабильной сети к `supabase.co` используется единый надёжный канал Management API — создание auth-пользователей прямым SQL, проверка RPC/RLS через `set local role authenticated` + `set local request.jwt.claims` в одной транзакции (`scripts/e2e_evaluation_sql.py`). HTTP-путь (`scripts/e2e_evaluation.py`) ждёт либо `SUPABASE_JWT_SECRET` в `.env`, либо стабильной сети.
23. **Транзакционность/SQL:** мульти-стейтмент пакет Management API выполняется как единая неявная транзакция (SET LOCAL/RESET ROLE персистят в рамках пакета); контекст JWT воспроизводится GUC `request.jwt.claims` → `auth.uid()` работает.

## Открытые вопросы (не блокируют MVP)

- `?` Score политика → MVP: 0 очков за `unknown` достижения.
- ~~Первое достижение пользователя (нет personal scale)~~ **решено (D6): якорь = позиция 1**.
- Вес оценок по опыту → будущие версии.
- **Email-конфирмация включена** в Supabase (при регистрации GoTrue отправляет письмо; замечен rate limit писем). Код обрабатывает оба случая: session есть → вход; нет → сообщение «подтвердите email».
- Уникальность `username`: только БД-индекс; коллизия → ошибка `handle_new_user` (обработать в D10).
- `/auth/recover` без `redirect_to`; письма ведут на дефолтный URL (настроить в D10).
- `SUPABASE_JWT_SECRET` пуст и через Management API не доступен → самоподписанные JWT для локальных HTTP live-прогонов недоступны (используется SQL-канал).

## Следующие шаги

1. D8 — Player Score + Leaderboard: страница `/leaderboard` (achievement + player), рендер ранга/очков, «?» для unknown, `rankings`/`scoring` в docs.
2. D9 — Reports: форма жалобы + очередь модератора.
3. D10 — Polish: CSRF-токен, обработка коллизий `username`, `redirect_to` для `/auth/recover`, повтор `resolve_user` при сетевых ошибках, финальный E2E-прогон.
4. Доп: live-проверка файловой части D5 (upload через приложение + signed URL) и HTTP-пути D6 — при стабильной сети.

## Риски на горизонте

- Ранкинг чувствителен к tie-breaking и атомарности пересчёта → покрыть тестами раньше всего (D7).
- CSRF для форм: MVP — SameSite=Lax; полноценный CSRF-токен на D10.
- Storage-политики bucket `proofs` (upload/read) настроены и применены; live-проверка загрузки файла через приложение отложена из-за сети.
- **Нестабильная сеть/прокси** к `supabase.co` (auth/v1/token ReadTimeout, admin/users транзиентные 500, REST RemoteProtocolError): live-проверки — через надёжный Management API (SQL-канал) или с ретраями.
- `resolve_user` при сетевой ошибке возвращает `None` → запрос идёт как анонимный (в E2E это выглядело как «потеря сессии»); для MVP ок, в D10 можно добавить повтор.