# Active Context — GAL

## Текущий фокус

**D11 — Bugfix по итогам bug-hunt 2026-09-28 завершён.** MVP v1.0 (D1–D10) + D11 реализованы. Написана миграция `010_harden_achievements_and_ranks.sql` (корректная блокировка подделки колонок `achievements`, гард `recompute_ranks()` через `is_moderator()`, усиление `submit_evaluation` — блокировка всех оценок при >=3 и единообразный переход в `ranked`). Исправлены критичные/medium места: валидация JWT в `auth`, парсинг позиции в `evaluations`, раздельная обработка создания и proof в `achievements`, корректная работа с `UploadedFile.stream` в `completions`, надёжный выход `e2e_reports_sql.py` при ошибках, фикс заголовка в `moderation/queue.html`, чистка скриптов. Все тесты 94/94 пройдены, `ruff check`/`format` — чисто. Коммит `842865f` в `main`.

## Статус задач

- D11 (Bugfix по bug-hunt 09-28): **completed**
  - [x] CRITICAL: запрет подделки `rank`/`ranking_status`/`average_position`/`evaluation_count` через REST — ограничение UPDATE `achievements` разрешёнными колонками (новая миграция 010)
  - [x] CRITICAL: `recompute_ranks()` — публичный execute убран у `authenticated`, добавлен гард `public.is_moderator()` (defense-in-depth), execute остался у `service_role`
  - [x] HIGH: `set_session()` на битой куке — `IndexError` не возникает (проверка `looks_like_jwt` в `set_session`/`user_client_from_request` + расширение `AUTH_ERRORS`)
  - [x] HIGH: `moderation/queue.html` — заголовок «Модерация»
  - [x] MEDIUM: парсинг позиции в `evaluations._parse_position` — строгий ascii/digit + лимит
  - [x] MEDIUM: `reports` — проверка существования достижения в POST
  - [x] MEDIUM: `achievements.create` — разделение обработки ошибок create/proof (исключает дубли при ре-сабмите)
  - [x] MEDIUM: `submit_evaluation` — при `count >= 3` всегда переводит в `ranked`, выставляет `rank_order`/`rank` при необходимости, блокирует все оценки достижения
  - [x] MEDIUM: `completions.attach_proof` — корректная работа с файлом (`seek(0)`)
  - [x] MEDIUM: `scripts/e2e_reports_sql.py` — корректный ненулевой код при неуспехе, удаление `_gal_e2e_results` в finally
  - [x] MEDIUM: `vercel.json` — `maxLambdaSize: 250mb`; `requirements.txt` — без dev-зависимостей в прод-бандле (dev перенесены при желании в `requirements-dev.txt`)
- D1–D10: **completed**

## Ключевые изменения

1. `migrations/010_harden_achievements_and_ranks.sql` — новая миграция: `revoke update` на `achievements` → `grant update` по whitelist колонок; `recompute_ranks` без публичного execute у `authenticated`, гард `is_moderator()`; `submit_evaluation` усиливает фиксацию `ranked` и блокировку оценок.
2. `app/services/auth.py` — `looks_like_jwt` используется в `set_session` (через `new_user_client` path) и `user_client_from_request`; `MalformedSessionTokenError` в `AUTH_ERRORS`.
3. `app/routers/evaluations.py` — `_parse_position` защищён от не-ASCII цифр и экстремально длинных строк.
4. `app/routers/achievements.py` — создание и proof обрабатываются раздельно (редирект на карточку при ошибке proof).
5. `app/services/completions.py` — `seek(0)` при наличии файла (исключает проблемы при повторном чтении).
6. `scripts/e2e_reports_sql.py` — явный `return 1` при ошибках, cleanup `_gal_e2e_results`.
7. `app/templates/moderation/queue.html` — корректный заголовок страницы.

## Следующие шаги

- Применить миграцию `010_harden_achievements_and_ranks.sql` в проде (Management API / `scripts/apply_migrations.py`).
- При необходимости прогнать live-проверки по оценкам/жалобам (SQL-канал) после применения миграции.
- Дальнейшие доработки — только post-MVP (пагинация, история модерации и пр.), не блокирующие текущий релиз.

## Риски

- После изменения `recompute_ranks` (гард `is_moderator()`) прямой вызов из-под `authenticated` более не работает; `submit_evaluation` вызывает SD-функцию (внутри транзакции) — корректно. Внешние скрипты, вызывающие `recompute_ranks()` напрямую от имени `authenticated`, потребуют либо `service_role`, либо прав модератора.
- Изменение whitelist UPDATE `achievements` не затрагивает логику приложения (только разрешённые поля редактируются через существующие пути).
