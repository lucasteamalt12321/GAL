# System Patterns — GAL

## Архитектура

```text
                    ┌─────────────────┐
                    │     Browser     │
                    │  HTML/CSS/JS    │
                    └────────┬────────┘
                             │ HTTPS
                             ▼
                    ┌─────────────────┐
                    │     Vercel      │
                    │    FastAPI      │
                    │     Python      │
                    └────────┬────────┘
                             │
                ┌────────────┼────────────┐
                ▼            ▼            ▼
          ┌──────────┐ ┌──────────┐ ┌──────────┐
          │ Supabase │ │ Supabase │ │ Supabase │
          │ Postgres │ │   Auth   │ │ Storage  │
          └──────────┘ └──────────┘ └──────────┘
```

Принцип: FastAPI = бизнес-логика; Supabase = инфраструктура (данные, аккаунты, файлы).

## Структура проекта

```text
gal/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── presentation.py     ← подписи enum/дат, склонения
│   ├── database/connection.py
│   ├── routers/
│   │   ├── auth.py
│   │   ├── users.py
│   │   ├── settings.py     ← профиль и смена пароля
│   │   ├── achievements.py
│   │   ├── completions.py
│   │   ├── evaluations.py
│   │   ├── rankings.py
│   │   ├── reports.py
│   │   └── moderation.py
│   ├── services/
│   │   ├── ranking.py      ← ядро системы (player_score здесь)
│   │   ├── achievements.py
│   │   ├── evaluation.py
│   │   ├── moderation.py
│   │   ├── reports.py
│   │   ├── profiles.py
│   │   └── completions.py
│   └── schemas/            ← зарезервировано под Pydantic-схемы API
├── templates/
├── static/
├── tests/
├── migrations/
├── .github/workflows/ci.yml
├── requirements.txt
├── vercel.json
├── .env.example
└── README.md
```

## Паттерны

### 1. Источник истины

- PostgreSQL — первичные данные (users, achievements, completions, evaluations, proofs, moderation, reports).
- Storage — файлы доказательств.
- **Rank** и **Player Score** — производные, пересчитываются из первичных данных, не хранятся как канон.

### 2. Данные: Rank vs Average Position

- `average_position` — среднее арифметическое оценок пользователей.
- `rank` — фактическое место в рейтинге (может отличаться от average из-за tie-resolution и соседних позиций).

### 3. Жизненный цикл сущностей

- **Achievement:** `pending → published` (после модерации), либо `rejected`; `deleted` — автором (только `pending`) или модератором (hide/принятие жалобы).
- **Completion:** `pending → approved` / `rejected` (модерация proof). `UNIQUE(achievement, user)`. При reject/hide достижения его pending-заявки отклоняются.
- **Evaluation:** `UNIQUE(achievement, user)`. `locked=true` после перехода достижения `unknown → ranked`.
- **Report:** `pending → accepted` / `rejected`.
- **Moderation review:** пишется при каждом решении (achievement/completion), только от имени текущего модератора (RLS 013), виден модераторам в `/moderation/history`.

### 4. Ranking recompute (цепочка при новой оценке)

```text
1. Save evaluation
2. Calculate averages
3. Sort achievements (average ASC)
4. Resolve equal positions (фиксированный случайный порядок)
5. Assign ranks
6. Update achievement ranks
7. Recalculate player scores
```

Пересчёт должен быть атомарным (в одной транзакции/функции).

### 5. Security / RLS

- User: только свои achievements/completions/evaluations/proofs; чтение публичных; нельзя менять чужие оценки и rank.
- Moderator: проверка submissions, approve/reject/hide, ревью reports, история модерации.
- Backend не доверяет frontend: `rank`, `points`, `approved` вычисляет только сервер.
- proofs видны владельцу и модератору; публичная политика `proofs_select_approved_public` удалена в 013.
- Публикация достижения без `approved`-выполнения создателя с proof запрещена deferred constraint trigger-ом (013).
- `SERVICE_ROLE_KEY` никогда не попадает во frontend.

### 6. Оценка достижения (Personal Scale)

- Можно только при наличии `approved` completion.
- Пользователь размещает новое достижение относительно собственных выполненных.
- MVP: допустимо оценить относительно глобальной шкалы, если у пользователя нет личной шкалы (исключение).

### 7. Creator Evaluation

- Создатель автоматически становится первым выполнившим и первым evaluater-ом при публикации.
- Его оценка строится на его реальных достижениях (personal scale).

## Главная цепочка данных

```text
Achievement → Creator Proof → Moderation → Published
   → User Completion → Completion Proof → Moderation
   → Personal Evaluation → Average Position → Global Rank
   → 1000 / Rank → Player Leaderboard
```