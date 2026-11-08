# Project Brief — Global Achievement List (GAL)

## Цель

Глобальная платформа для рейтинга человеческих достижений. Сравнение достижений из разных сфер (Geometry Dash, музыка, шахматы, программирование, Minecraft, образование, спорт) через единую относительную шкалу сложности, построенную на личных сравнениях пользователей.

> **Статус: MVP v1.0–v1.5 завершены.** Код задеплоен, миграции 013–020 применены. Вход через LTHub, «Мои заявки», сложность при создании/прохождении, сравнительный выбор и живые оценки (новые заявки меняют топ). UI оформлен в стиле VKUI (light/dark), ошибки Storage показываются пользователю понятным текстом. Создать своё достижение можно только после оценки опубликованного достижения (проверка в UI, сервисе и RLS). `pytest` — 165 passed, `ruff check`/`format` — чисто.

## Рамки v1.2 (D13) — Bradley–Terry

- построение попарных сравнений из индивидуальных шкал (все пары, вес `1/(k-1)`);
- оценка глобальных сил регуляризованным MM-алгоритмом (prior κ=0.5, центрирование);
- колонки `bt_strength`, `bt_rating` (1000 + 400·s/ln10), `bt_comparisons`, `bt_updated_at`;
- ранжирование по `bt_strength desc` с fallback на среднюю позицию;
- атомарное применение через RPC `apply_bt_ratings(jsonb)` (service role) + advisory lock;
- автопересчёт после оценок, модерации и принятия жалоб; ручной `scripts/recompute_bt.py`;
- эксперимент точности/устойчивости (`scripts/bt_experiment.py`) и docs/bradley_terry.md.

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
| D3  | Authentication + LThub OAuth      | completed | 8   |
| D4  | Achievements CRUD                 | completed | 8   |
| D5  | Proofs + Moderation               | completed | 12  |
| D6  | Evaluation + difficulty flow     | completed | 12  |
| D7  | Ranking Engine (тесты)            | completed | 12  |
| D8  | Player Score + Leaderboard        | completed | 8   |
| D9  | Reports                           | completed | 4   |
| D10 | Polish (UI, security, errors)     | completed | 4   |
| D11 | Bugfix по итогам bug-hunt 09-28   | completed | 5   |
| D12 | v1.1: функции, безопасность, CI   | completed | 5   |
| D13 | v1.2: Bradley–Terry ранкинг       | completed | 10  |

## Success Criteria MVP

Пользователь способен полностью пройти цикл: регистрация → создание достижения → proof → модерация → публикация → выполнение другим пользователем → proof → модерация → личная оценка → попарные сравнения → глобальная сила Bradley–Terry → ранг → очки → player leaderboard.
