# Project Brief — Global Achievement List (GAL)

## Цель

Глобальная платформа для рейтинга человеческих достижений. Сравнение достижений из разных сфер (Geometry Dash, музыка, шахматы, программирование, Minecraft, образование, спорт) через единую относительную шкалу сложности, построенную на личных сравнениях пользователей.

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

| ID  | Deliverable                       | Status       | Вес |
|-----|-----------------------------------|--------------|-----|
| D1  | Foundation + Memory Bank          | completed    | 5   |
| D2  | Database Schema + RLS             | completed    | 10  |
| D3  | Authentication                    | completed    | 10  |
| D4  | Achievements CRUD                 | completed    | 10  |
| D5  | Proofs + Moderation               | completed    | 15  |
| D6  | Evaluation Engine + Creator Eval  | completed    | 15  |
| D7  | Ranking Engine (тесты)            | pending      | 15  |
| D8  | Player Score + Leaderboard        | pending      | 10  |
| D9  | Reports                           | pending      | 5   |
| D10 | Polish (UI, security, errors)     | pending      | 5   |

### Ключевые правила deliverables

- Каждый новый пункт ТЗ / scope change добавляется в этот список до или одновременно с реализацией.
- После задачи, влияющей на готовность, статусы обновляются.
- Если deliverables рассинхронизированы с кодом — восстановить перед расчётом процента.

## Success Criteria MVP

Пользователь способен полностью пройти цикл: регистрация → создание достижения → proof → модерация → публикация → выполнение другим пользователем → proof → модерация → личная оценка → глобальный рейтинг → очки → player leaderboard.