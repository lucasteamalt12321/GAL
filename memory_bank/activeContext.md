# Active Context — GAL

## Текущий фокус

Инициализация Memory Bank (задача D1, шаг 0 плана). Проект находится на старте: репозиторий GAL пуст (есть только `AGENTS.md`).

## Статус задач

- D1 (Foundation + Memory Bank): **in_progress**
  - [x] Инициализация Memory Bank (текущий файл)
  - [ ] `docs/README.md`
  - [ ] Каркас FastAPI: `main.py`, `config.py`, `connection.py`
  - [ ] `requirements.txt`, `vercel.json`, `.env.example`, `.gitignore`
  - [ ] Базовая структура `templates/base.html` + `static/`

## Принятые архитектурные решения

1. **Frontend:** Jinja2 + минимальный JS (серверный рендеринг).
2. **Backend:** Python + FastAPI (monolith: один backend + одна БД).
3. **DB/Auth/Storage:** Supabase (PostgreSQL + Auth + Storage).
4. **Deploy:** Vercel.
5. **Draft статус:** отсутствует — только `pending → published` (упрощение MVP).
6. **Creator Evaluation:** включено — создатель автоматически первый evaluater своего достижения.
7. **Дубликаты:** разрешены вручную модератором (без автодетекта в MVP).
8. **Ranking Engine** — отдельный модуль `app/services/ranking.py`, покрытый тестами.
9. **Player Score** — производное значение (`1000 / rank`), не хранится; пересчитывается.
10. **Tie-breaking v1.0:** при равном average порядок фиксируется случайно при разрешении конфликта (не каждый просмотр).

## Открытые вопросы (не блокируют MVP)

- `?` Score политика → MVP: 0 очков за `unknown` достижения.
- Первое достижение пользователя (нет personal scale для сравнения) → пока планируется разрешить оценку относительно глобальной шкалы как исключение.
- Weighted average / опыт пользователя → будущие версии.

## Следующие шаги

1. Создать `docs/README.md` и каркас проекта (D1).
2. Подготовить миграцию `001_init.sql` (D2).
3. Настроить Supabase проект (новый) + переменные окружения.

## Риски на горизонте

- Ранкинг очень чувствителен к tie-breaking и атомарности пересчёта → покрыть тестами раньше всего.
- RLS Supabase: критично правильно разделить права user/moderator.
- Auth cookie-flow Supabase + FastAPI: требует аккуратной настройки.