# Active Context — GAL

## Текущий фокус

**D11 — Bugfix по итогам bug-hunt 2026-09-28: завершён и доставлен в прод** (2026-10-18/19). MVP v1.0 (D1–D10) + D11 реализованы; изменения запушены в `main` (коммиты `57e48e2`, `4eeb269`); автодеплой Vercel подтверждён (`gal-mygfx8c73`, 18.10 21:31, Ready, alias `gal-inky.vercel.app`); миграции `008`–`012` применены к прод-БД Supabase `wiwyitafoprxmlyndkwe`. Live-e2e на проде: оценки **16/16**, жалобы **5/5**, тестовые данные убраны. `pytest` — 94 passed, `ruff check`/`format` — чисто.

**Разрешение конфликта миграций:** моя `010_harden_achievements_and_ranks.sql` (коммит `842865f`) **удалена** — сломана (`LANGUAGE SQL` с PL/pgSQL-синтаксисом; guard `is_moderator()` внутри `recompute_ranks()` ломал бы оценки обычных пользователей). Приняты чужие миграции (28.09.2026, другой программист): `010_ranking_integrity.sql` + `011_evaluation_lock.sql` — полнее и корректнее (`recompute_ranks_internal()` без guard + guarded-обёртка, trigger `guard_ranking_fields`, data-fix locked). Добавлена новая `012_revoke_internal_execute.sql`. **Порядок применения критичен: 008 → 009 → 010 → 011 → 012** (008 содержит `create or replace` устаревших `submit_evaluation`/`recompute_ranks`, после неё обязательно переприменение 010/011).

**Инцидент 18.10:** прод-проект Supabase был `INACTIVE` (compute остановлен) — все страницы приложения были недоступны. Восстановлен через `POST /v1/projects/{ref}/restore` (201 → `COMING_UP` → `CHANNEL_UP`). Ложная запись memory bank «008/009 применены к проду» исправлена — фактически они применены только в этой сессии.

## Статус задач

- D11 (Bugfix по bug-hunt 09-28): **completed**, доставлен в прод
  - [x] CRITICAL: запрет подделки `rank`/`ranking_status`/`average_position`/`evaluation_count` через REST — закрыто чужой `010_ranking_integrity.sql` (trigger `guard_ranking_fields`; column-level REVOKE из 008 не действует на table-level grant из 002, поэтому триггер вместо прав)
  - [x] CRITICAL: `recompute_ranks()` — реализовано чужой `010_ranking_integrity.sql`: `recompute_ranks_internal()` + guarded-обёртка (моя 010_harden удалена как сломанная)
  - [x] HIGH: `set_session()` на битой куке — `IndexError` не возникает (проверка `looks_like_jwt` в `set_session`/`user_client_from_request` + расширение `AUTH_ERRORS`)
  - [x] HIGH: `moderation/queue.html` — заголовок «Модерация»
  - [x] MEDIUM: парсинг позиции в `evaluations._parse_position` — строгий ascii/digit + лимит
  - [x] MEDIUM: `reports` — проверка существования достижения в POST
  - [x] MEDIUM: `achievements.create` — разделение обработки ошибок create/proof (исключает дубли при ре-сабмите)
  - [x] MEDIUM: `submit_evaluation` — блокировка оценок (`locked`) при >=3 — закрыто чужой `011_evaluation_lock.sql`
  - [x] MEDIUM: `completions.attach_proof` — корректная работа с файлом (`seek(0)`)
  - [x] MEDIUM: `scripts/e2e_reports_sql.py` — корректный ненулевой код при неуспехе, удаление `_gal_e2e_results` в finally
  - [x] MEDIUM: `vercel.json` — `maxLambdaSize: 250mb`; `requirements.txt` — без dev-зависимостей в прод-бандле (dev в `requirements-dev.txt`)
  - [x] Доставка: push `57e48e2`/`4eeb269` → автодеплой Vercel; миграции 008–012 применены к проду; live-e2e 16/16 + 5/5; HTTP 200 (`/health`, `/`, `/achievements`, `/leaderboard`, `/auth/login`, `/docs`)
- D1–D10: **completed**

## Ключевые изменения

1. Миграции прод-БД: `008_hardening.sql` (revoke execute SD-RPC, deferred recompute, запрет REST-обновления служебных колонок), `009_retry_completion_rpc.sql` (retry/cleanup rejected completion), `010_ranking_integrity.sql` + `011_evaluation_lock.sql` (чужие, приняты), `012_revoke_internal_execute.sql` (revoke execute на `recompute_ranks_internal()` от `anon`/`authenticated`/`service_role` — default privileges Supabase выдают execute всем ролям, revoke от PUBLIC не снимает; проверено: до 012 anon вызывал, после — `42501`).
2. `app/services/auth.py` — `looks_like_jwt` используется в `set_session` и `user_client_from_request`; `MalformedSessionTokenError` в `AUTH_ERRORS`.
3. `app/routers/evaluations.py` — `_parse_position` защищён от не-ASCII цифр и экстремально длинных строк.
4. `app/routers/achievements.py` — создание и proof обрабатываются раздельно (редирект на карточку при ошибке proof).
5. `app/services/completions.py` — `seek(0)` при наличии файла.
6. `scripts/e2e_reports_sql.py` — явный `return 1` при ошибках, cleanup `_gal_e2e_results`; `scripts/e2e_evaluation_sql.py` — удалён чек `rls_unlocked_self_update` (задублирован `rls_locked_update_blocked`, после 008 прямой UPDATE падает с permission denied).
7. `app/templates/moderation/queue.html` — корректный заголовок страницы.

## Следующие шаги

- Пост-MVP доработки (пагинация, история модерации и пр.) — не блокируют релиз.
- При новых миграциях применять к проду строго в нумерованном порядке и сразу перепроверять live-e2e.

## Риски

- Локальный DNS отравлен/нестабилен: `*.supabase.co` и часть доменов не резолвятся, `*.vercel.app` резолвится в фиктивный IP. Обход: DoH (`dns.google/resolve`) + `curl --resolve`; рабочий IP на 19.10 — `216.198.79.195` (может смениться, перерезолвить). `api.supabase.com` и vercel CLI работают напрямую.
- `recompute_ranks_internal()` закрыт от `anon`/`authenticated`/`service_role` execute (012); служебные колонки `achievements` защищены trigger `guard_ranking_fields` (010). Изменение whitelist не затрагивает логику приложения.
