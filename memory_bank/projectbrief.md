# Project Brief — Global Achievement List (GAL)

## Цель

Глобальная платформа для рейтинга человеческих достижений. Сравнение достижений из разных сфер (Geometry Dash, музыка, шахматы, программирование, Minecraft, образование, спорт) через единую относительную шкалу сложности, построенную на личных сравнениях пользователей.

> **Статус: MVP v1.0 завершён; v1.1 (D12) в работе.** D1–D11 completed, D12 — финальная полировка, недостающие функции, безопасность и CI. Код v1.1.0 закоммичен и задеплоен на прод. `pytest` — 121 passed; `ruff format` чист, но `ruff check` падает на новых правилах (UP017/FURB162) — CI красный.

## Рамки v1.1 (D12)

- редактирование/удаление своих достижений в статусе `pending`;
- скрытие достижения модератором (soft-delete) и история решений;
- настройки профиля и смена пароля; завершение сброса пароля;
- пагинация и поиск в каталоге, пагинация лидербордов;
- 404/403-страницы, локализация, мобильная вёрстка, JS-валидация файла;
- приватность proofs (RLS), инвариант «публикация только с proof создателя»;
- GitHub Actions (ruff + pytest) и синхронизация документации.


## Рамки MVP (v1.0)

Стек: Python + FastAPI + Jinja2 + Supabase (PostgreSQL + Auth + Storage) + Vercel.

MVP покрывает базовую цепочку:

```text
Achievement → Proof → Moderation → Evaluation → Global Rank → Player Score → Leaderboard
```

## Что НЕ входит в MVP

- AI-модерация;
- weighted average / опыт пользователя;
- reputation score;
- исторический график рейтинга;
- автоматическое определение дубликатов;
- социальная сеть / чаты;
- внутренние достижения сайта;
- интеграция API игр;
- мобильное приложение;
- статус Draft (только `pending → published`).

## Draft Decision (Draft отсутствует)

- User создаёт `pending` achievement
- Creator обязан приложить proof собственного выполнения
- Achievement публикуется только после модерации
- Creator автоматически становится первым выполнившим и первым evaluater-ом

---

## Project Deliverables

Сумма весов = 100. Процент выполнения считается только по этому списку.

| ID  | Deliverable                       | Status    | Вес |
|-----|-----------------------------------|-----------|-----|
| D1  | Foundation + Memory Bank          | completed | 4   |
| D2  | Database Schema + RLS             | completed | 8   |
| D3  | Authentication                    | completed | 8   |
| D4  | Achievements CRUD                 | completed | 8   |
| D5  | Proofs + Moderation               | completed | 12  |
| D6  | Evaluation Engine + Creator Eval  | completed | 12  |
| D7  | Ranking Engine (тесты)            | completed | 12  |
| D8  | Player Score + Leaderboard        | completed | 8   |
| D9  | Reports                           | completed | 4   |
| D10 | Polish (UI, security, errors)     | completed | 4   |
| D11 | Bugfix по итогам bug-hunt 09-28   | completed | 10  |
| D12 | v1.1: функции, безопасность, CI   | in_progress | 10 |

## Success Criteria MVP

Пользователь способен полностью пройти цикл: регистрация → создание достижения → proof → модерация → публикация → выполнение другим пользователем → proof → модерация → личная оценка → глобальный рейтинг → очки → player leaderboard.
