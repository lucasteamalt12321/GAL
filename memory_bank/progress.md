# Progress — GAL

## Общий прогресс

Процент выполнения (по `## Project Deliverables` в `projectbrief.md`): **100%** (D1–D11 completed).

## Deliverables статус

| ID  | Deliverable                       | Status    |
|-----|-----------------------------------|-----------|
| D1  | Foundation + Memory Bank          | completed |
| D2  | Database Schema + RLS             | completed |
| D3  | Authentication                    | completed |
| D4  | Achievements CRUD                 | completed |
| D5  | Proofs + Moderation               | completed |
| D6  | Evaluation Engine + Creator Eval  | completed |
| D7  | Ranking Engine (тесты)            | completed |
| D8  | Player Score + Leaderboard        | completed |
| D9  | Reports                           | completed |
| D10 | Polish (UI, security, errors)     | completed |
| D11 | Bugfix по итогам bug-hunt 09-28   | completed |

## Changelog

### 2026-10-18 — D11 Bugfix: все критические/ключевые находки закрыты

Реализованы и закоммичены исправления по баг-ханту 2026-09-28. `pytest` — **94 passed**, `ruff check`/`format` — чисто. Коммит `842865f`.

**Критические:**
- `migrations/010_harden_achievements_and_ranks.sql`: блокировка подделки `rank`/`ranking_status`/`average_position`/`evaluation_count` — `revoke update on achievements from authenticated; grant update` только по разрешённым колонкам (`title, description, requirements, category_id, status`).
- `migrations/010_harden_achievements_and_ranks.sql`: `recompute_ranks()` — убран публичный `execute` у `authenticated`, добавлен гард `public.is_moderator()` (defense-in-depth), `execute` оставлен у `service_role`. `submit_evaluation` продолжает вызывать SD-функцию.

**High:**
- `app/services/auth.py`: `looks_like_jwt` используется в `set_session` и `user_client_from_request`; битая кука не передаётся в supabase_auth → нет `IndexError`. `MalformedSessionTokenError` добавлен в `AUTH_ERRORS`.
- `app/templates/moderation/queue.html`: заголовок страницы исправлен на «Модерация».

**Medium (ключевые):**
- `app/routers/evaluations.py`: `_parse_position` — строгий парсинг (`isascii` + `isdigit`) + ограничение диапазона.
- `app/routers/reports.py`: добавлена проверка существования достижения в POST перед созданием жалобы.
- `app/routers/achievements.py`: создание достижения и прикрепление proof создателя обрабатываются раздельно (при ошибке proof редирект на карточку, без повторного создания).
- `migrations/010_harden_achievements_and_ranks.sql`: `submit_evaluation` при `count >= 3` всегда переводит достижение в `ranked`, при необходимости проставляет `rank_order`/`rank`, блокирует **все** оценки достижения (`locked=true`).
- `app/services/completions.py`: при наличии файла вызывается `uploaded.file.seek(0)`.
- `scripts/e2e_reports_sql.py`: при не-201 возвращается ненулевой код выхода, `_gal_e2e_results` удаляется в `finally`.
- `vercel.json`: `maxLambdaSize` → `250mb`. `requirements.txt` очищен от dev-зависимостей (pytest/ruff не тянутся в прод-бандл).

**Прочее:**
- `app/services/achievements.py`, `app/services/reports.py`, `app/services/evaluation.py`, `app/routers/reports.py`, шаблоны `achievements/create.html`, `achievements/detail.html`, `base.html` — мелкие правки, соответствующие баг-ханту (в т.ч. экранирование в поиске username, обработка edge-кейсов).
- `scripts/e2e_evaluation_sql.py`, `tests/test_reports.py` — синхронизированы с правками.

## last_checked_commit

`842865f D11: harden achievements/ranks + auth guard + evaluations parsing + reports guard + achievements create-split + completions stream + e2e + template + config tweaks` (2026-10-18)
