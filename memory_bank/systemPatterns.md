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
│   │   ├── oauth.py        ← GAL как OAuth 2.0 provider (PKCE)
│   │   └── moderation.py
│   ├── services/
│   │   ├── bradley_terry.py ← попарные сравнения + MM-оценка
│   │   ├── bt_recompute.py  ← пересчёт по БД через service role
│   │   ├── ranking.py      ← порядок рангов по BT, player_score
│   │   ├── oauth_provider.py ← коды/токены провайдера GAL
│   │   ├── lthub_accounts.py ← LTHub identity → Supabase-сессия
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

### 2. Данные: Rank vs BT-сила

- `bt_strength` — логит-сила Bradley–Terry (`s_i`), больше = сложнее; `bt_rating = 1000 + 400·s/ln10` — Elo-подобное отображение.
- `bt_comparisons` — число попарных сравнений достижения (надёжность).
- `bt_rating_se`, `bt_rating_ci_low`, `bt_rating_ci_high` — погрешность и 95% ДИ (кластерный бутстрэп по шкалам); `NULL` при недостатке данных.
- `average_position` — устаревшее среднее позиций, остаётся как fallback и tie-break, если BT ещё не посчитан.
- `rank` — фактическое место: `bt_strength desc, average_position asc, rank_order asc, id asc`.
- `permanent_rank` (вьюха `public.public_achievements`) — постоянное место; `display_rank`/`rank` — место в общем топе с учётом временных позиций; `is_temporary` и `in_leaderboard` — признаки вхождения.
- `achievement_temporary_ratings` — временная сила одобренной заявки топ-5 до третьей обычной оценки; не канон и снимается автоматически.

### 3. Жизненный цикл сущностей

- **Achievement:** `pending → published` (после модерации), либо `rejected`; `deleted` — автором (только `pending`) или модератором (hide/принятие жалобы).
- **Completion:** `pending → approved` / `rejected` (модерация proof). `UNIQUE(achievement, user)`. При reject/hide достижения его pending-заявки отклоняются; автор может переотправить rejected-заявку (reset RPC + замена proof) из карточки или `/submissions`. Флаг `proofs_visible` (028) скрывает доказательство от остальных; верификация создателя всегда видима.
- **Evaluation:** `UNIQUE(achievement, user)`. Новые оценки принимаются в любой момент, включая `ranked`; после каждой оценки пересчитываются среднее, ранги и BT. `locked` оставлен в схеме для совместимости, но всегда false.
- **Top-5 application:** создаётся чекбоксом вместе с прохождением (`pending`, `completion_id` — 031) → `approved` (временная позиция; «Принять как сложное», место 1–6 выбирает модератор, по умолчанию запрошенное) / `rejected` (с причиной; в т.ч. при обычном принятии/отказе прохождения) / `cancelled` — автором или модератором. Независимая экспертиза отключена; legacy-статус `expert_review` показывается как «На рассмотрении» и доступен для решения. Одна активная заявка на достижение; третья обычная оценка снимает временную запись (`converted`).
- **Report:** `pending → accepted` / `rejected`.
- **Moderation review:** пишется при каждом решении (achievement/completion), только от имени текущего модератора (RLS 013), виден модераторам в `/moderation/history`.

### 4. Ranking recompute (Bradley–Terry + временный топ-5)

```text
1. submit_evaluation сохраняет позицию в личной шкале (SQL, атомарно)
2. Python собирает все шкалы: published achievements + evaluations
3. Строит попарные сравнения (A выше B ⇒ A сложнее), вес пары 1/(k-1);
   каждая пара — отдельное соревнование (win/loss), ни одно не отбрасывается
   (кроме равных позиций и шкал из одного достижения)
4. MM-оценка сил: θ_i ← (W_i+α)/(Σ n_ij/(θ_i+θ_j)+α/(θ_i+1)), центрирование
5. Кластерный бутстрэп по пользователям: SE и 95% percentile CI (≥3 шкал, ≥5 реплик)
6. RPC apply_bt_ratings(jsonb): пишет bt_* (включая se/ci) и вызывает recompute_ranks_internal()
7. Ранги: bt_strength desc, fallback average_position/rank_order/id; UI показывает «± SE · 95% ДИ» при ≥2 сравнениях
8. recompute_temporary_ratings: midpoint между bt_strength соседей места
   модератора в рабочем списке (ranked-топ + ранее одобренные временные
   в порядке решений; края — экстраполяция по разрыву соседней пары,
   TOP5_MIN_GAP=0.05 / TOP5_EDGE_GAP=0.5), RPC apply_temporary_ratings
   (030: адресное удаление не-eligible строк + upsert по achievement_id,
   а не очистка всего кэша — stale payload не стирает свежие строки)
9. Общий display_rank = ranked + временные; permanent_rank не меняется
10. Очки игроков выводятся только из permanent_rank (1000/rank), временные #N* не учитываются
```

Пересчёт запускается после оценки, модерации и принятия жалобы; запись
атомарна (advisory lock), ошибка пересчёта не ломает пользовательский сценарий.
При росте данных полный пересчёт нужно вынести в батч/материализацию.

### 5. Security / RLS

- User: только свои achievements/completions/evaluations/proofs; чтение публичных; нельзя менять чужие оценки и rank.
- Moderator: проверка submissions, approve/reject/hide, ревью reports, история модерации.
- Backend не доверяет frontend: `rank`, `points`, `approved` вычисляет только сервер.
- proofs видны владельцу и модератору; публичная политика `proofs_select_approved_public` удалена в 013.
- Публичные RPC списков прошедших отдают proofs только для `proofs_visible = true`, либо когда вызывающий — владелец прохождения (`auth.uid()`) или модератор. Триггер 028 принудительно делает proof создателя видимым.
- Публикация достижения без `approved`-выполнения создателя с proof запрещена deferred constraint trigger-ом (013).
- `bt_*` меняются только RPC `apply_bt_ratings` (security definer, execute у `service_role`); guard-триггер откатывает прямые изменения.
- LTHub OAuth: публичный клиент + PKCE S256, одноразовые коды (TTL 5 мин), opaque tokens (30 дней); GAL проверяет state и создаёт Supabase-сессию через service role; привязка в `external_identities` недоступна anon/authenticated.
- GAL как OAuth-провайдер: `oauth_*` таблицы хранят только SHA-256-хеши кодов/токенов и доступны исключительно `service_role`; authorize требует активную сессию GAL. Клиенты — статический реестр в `app/services/oauth_provider.py` (без таблицы `oauth_clients`); redirect URI расширяются через `OAUTH_LTHUB_REDIRECT_URIS`.
- Все ответы получают security-заголовки (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, CSP `frame-ancestors 'none'`, HSTS в проде); CSRF-токен обязателен для POST.
- Внешние ссылки-доказательства допускаются только со схемой `http(s)://` (сервис + CHECK в БД).
- Публичные списки прошедших (023/026/028): `achievement_completers` и `user_completed_achievements` — `security definer`, отдают только approved-заявки опубликованных достижений, публичные поля профиля и proofs с учётом `proofs_visible` (без email и moderation_reviews); скрытые proofs опускаются для всех, кроме владельца и модераторов.
- Топ-5 (027/031): заявку подаёт автор достижения или владелец прохождения (guard INSERT + RLS); модератор не может выставить место собственной заявке; ручное место модератора хранится отдельно от `evaluations` и не меняет `evaluation_count`; legacy-голоса экспертов игнорируются при пересчёте; каждое решение пишется в `top5_events`; временные силы пишет только `apply_temporary_ratings` (service role).
- `SERVICE_ROLE_KEY` никогда не попадает во frontend.

### 6. Оценка достижения (Personal Scale)

- Можно только при наличии `approved` completion.
- Пользователь размещает новое достижение относительно собственных выполненных.
- MVP: допустимо оценить относительно глобальной шкалы, если у пользователя нет личной шкалы (исключение).

### 7. Creator Evaluation

- Создатель автоматически становится первым выполнившим и первым evaluater-ом при публикации.
- Его оценка строится на его реальных достижениях (personal scale).

### 8. Публичные страницы

- `/users/{username}` — без авторизации: профиль (avatar, display_name, role, дата регистрации), очки `sum(1000/permanent_rank)` без временных мест, число пройденных, пройденные достижения (approved + published, с доказательствами) и созданные (для чужих — только published).
- `/achievements/{id}` — место и BT-сила/рейтинг, прошедшие пользователи с доказательствами, форма «Отправить прохождение» (для авторизованного без своей заявки; для гостя — вход).
- `/leaderboard` — топ достижений и игроков. В достижения входят ranked (с номером) и опубликованные с одной оценкой: их позиция предварительная (BT, затем среднее место) и выводится как «?», не занимая номер в нумерации.

## Главная цепочка данных

```text
Achievement → Creator Proof → Moderation → Published
   → User Completion → Completion Proof → Moderation
   → Personal Evaluation → Pairwise Comparisons
   → Bradley–Terry Strength → Global Rank
   → 1000 / Rank → Player Leaderboard
```