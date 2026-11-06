# Active Context — GAL

## Текущий фокус

**2026-11-06 — v1.1 (D12) завершён и проверен на проде.** CI зелёный на `7ed168d`: `ruff==0.17.4` закреплён, UP017/FURB162 исправлены, `pytest` — **123 passed**. Live-дефект 416 закрыт: каталог и оба лидерборда сначала запрашивают count, затем данные; `/achievements?page=2` и `/leaderboard?page=2` теперь 303 на валидную страницу вместо 503. Миграция `013` применена к проду (201) и проверена SQL: публичная политика proofs удалена, `player_leaderboard_page` и trigger `achievements_require_creator_proof` есть; deferred trigger отдаёт `PUBLISH_REQUIRES_CREATOR_PROOF`; anon не видит proofs; автор может soft-delete свой pending; RPC доступен anon; тестовые данные откатаны. Прод: `/openapi.json` — v1.1.0, ключевые маршруты 200, 404 — HTML.

**2026-11-05 — v1.1 (D12).** Закрыты: HTML-страницы 404/403, реальный лендинг вместо заглушки, навигация по ролям, русская локализация и подписи enum/дат, редактирование/удаление своих `pending`-достижений, скрытие достижения модератором, история модерации, настройки профиля, смена пароля и завершение сброса пароля, пагинация/поиск каталога и лидербордов, адаптивная вёрстка и JS (меню, pre-check файла, confirm). Безопасность/целостность: удалена публичная RLS-политика proofs, добавлен deferred trigger «публикация только с approved proof создателя», исправлено удаление битой cookie (`kwargs` в middleware), `NOT_FOUND` из `submit_evaluation` маппится в понятное сообщение, SupabaseNotConfiguredError → 503, добавлена миграция `013`, CI (ruff + pytest).

**Инцидент 2026-11-05:** прод-проект Supabase `wiwyitafoprxmlyndkwe` снова был `INACTIVE` — все страницы с данными отдавали 503 (лендинг/логин работали). Восстановлен через `POST /v1/projects/{ref}/restore` (200 → COMING_UP → ACTIVE_HEALTHY); после восстановления `/`, `/achievements`, `/leaderboard`, `/auth/*`, `/docs` снова 200. Прод-БД пустая (тестовые данные удалены ранее).

## Статус задач

- D12 (v1.1): **completed** — код v1.1.0 задеплоен, миграция 013 применена и проверена, CI зелёный.
  - [x] 404/403/503-страницы, JSON только для API-путей
  - [x] лендинг с топами (best-effort, без 503 при сбое БД)
  - [x] навигация: «Жалобы»/«Модерация» только модераторам, «Настройки»
  - [x] локализация, подписи статусов/причин/типов proof, даты, склонения
  - [x] редактирование/soft-delete своих pending-достижений
  - [x] скрытие достижения модератором + история модерации
  - [x] настройки профиля, смена пароля, `/auth/update-password`
  - [x] пагинация/поиск каталога; пагинация обоих лидербордов (`013`)
  - [x] пагинация за последней страницей: count-first + 303, без 416/503
  - [x] RLS: приватность proofs, авторство истории модерации
  - [x] инвариант «published ⇒ approved proof создателя» (013 + порядок approve)
  - [x] адаптивная вёрстка, контраст, фокус, skip-link; `main.js`
  - [x] CI ruff + pytest — зелёный (`7ed168d`)
  - [x] README/docs/memory bank
- D1–D11: **completed**

## Ключевые изменения (v1.1)

1. `app/presentation.py` — фильтры подписей и дат, регистрация в `templating.py`.
2. `app/main.py` — обработчики `StarletteHTTPException` (403/404/…), `SupabaseNotConfiguredError` → 503, лендинг с best-effort топами.
3. `app/routers/achievements.py`, `app/services/achievements.py` — пагинация/поиск каталога, edit/soft-delete своих pending, фильтрация статусов профиля.
4. `app/routers/moderation.py`, `app/services/moderation.py` — hide, история, approve только после approving creator completion с proof, отклонение заявок при reject/hide.
5. `app/routers/settings.py`, `app/services/profiles.py` — профиль и смена пароля.
6. `app/routers/auth.py`, `templates/auth/update_password.html` — завершение сброса пароля через Supabase Auth из браузера.
7. `migrations/013_ui_features_and_hardening.sql` — RLS-политики, deferred trigger, `player_leaderboard_page`.
8. `app/middleware.py` — `kwargs` cookie (удаление битой сессии с `max-age=0`).
9. `.github/workflows/ci.yml`, обновлённые README/docs.

## Следующие шаги

- Исследовательская рамка из `docs/idea.md`: перейти от среднего арифметического позиций к модели **Bradley–Terry** (попарные сравнения из индивидуальных шкал, MLE-оценка сил, устойчивость/сходимость). Требует отдельного решения: текущий MVP сознательно использует среднее; менять ранкинг нужно вместе с миграцией, пересчётом и e2e.
- Наполнить прод реальными достижениями и проверить полный цикл с обычным пользователем и модератором.
- Пост-MVP: уведомления, экспорт, расширенная аналитика.

## Риски

- **Локальная сеть блэкхолит TCP-диапазоны Supabase** (`8.47.69.0/24`, `8.6.112.0/24`): локальные live-e2e невозможны, проверка — через Management API и прод. Обход: VPN/прокси. Также локально нестабилен TLS до `*.vercel.app` — live-проверки делать через внешний fetch.
- **Порядок миграции 013 (уже соблюдён):** trigger блокирует публикацию без approved proof создателя. Новый код одобряет completion до публикации; старый код делал наоборот. При откате кода на версию < v1.1 возможны отказы публикации.
- **Сброс пароля** рассчитан на implicit-flow Supabase (токены в `#access_token`). Если в проекте включат PKCE, страницу нужно доработать под `?code=`.
- **Bradley–Terry vs текущий ранкинг:** смешивать нельзя; при внедрении нужно сохранить обратную совместимость `average_position`, `rank` и очков.
