# Active Context — GAL

## Текущий фокус

**2026-11-05 — v1.1 (D12): полировка, недостающие функции и безопасность.** По итогам аудита закрыты: HTML-страницы 404/403, реальный лендинг вместо заглушки, навигация по ролям, русская локализация и подписи enum/дат, редактирование/удаление своих `pending`-достижений, скрытие достижения модератором, история модерации, настройки профиля, смена пароля и завершение сброса пароля, пагинация/поиск каталога и лидербордов, адаптивная вёрстка и JS (меню, pre-check файла, confirm). Безопасность/целостность: удалена публичная RLS-политика proofs, добавлен deferred trigger «публикация только с approved proof создателя», исправлено удаление битой cookie (`kwargs` в middleware), `NOT_FOUND` из `submit_evaluation` маппится в понятное сообщение, SupabaseNotConfiguredError → 503, добавлена миграция `013`, CI (ruff + pytest). `pytest` — 121 passed, `ruff` чист.

**Инцидент 2026-11-05:** прод-проект Supabase `wiwyitafoprxmlyndkwe` снова был `INACTIVE` — все страницы с данными отдавали 503 (лендинг/логин работали). Восстановлен через `POST /v1/projects/{ref}/restore` (200 → COMING_UP → ACTIVE_HEALTHY); после восстановления `/`, `/achievements`, `/leaderboard`, `/auth/*`, `/docs` снова 200. Прод-БД пустая (тестовые данные удалены ранее).

## Статус задач

- D12 (v1.1): **in_progress** — код и тесты готовы, осталось: применить миграцию 013 к проду, задеплоить, проверить live, обновить `last_checked_commit`.
  - [x] 404/403/503-страницы, JSON только для API-путей
  - [x] лендинг с топами (best-effort, без 503 при сбое БД)
  - [x] навигация: «Жалобы»/«Модерация» только модераторам, «Настройки»
  - [x] локализация, подписи статусов/причин/типов proof, даты, склонения
  - [x] редактирование/soft-delete своих pending-достижений
  - [x] скрытие достижения модератором + история модерации
  - [x] настройки профиля, смена пароля, `/auth/update-password`
  - [x] пагинация/поиск каталога; пагинация обоих лидербордов (`013`)
  - [x] RLS: приватность proofs, авторство истории модерации
  - [x] инвариант «published ⇒ approved proof создателя» (013 + порядок approve)
  - [x] адаптивная вёрстка, контраст, фокус, skip-link; `main.js`
  - [x] CI ruff + pytest; README/docs/memory bank
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

- Применить `migrations/013` к проду (после деплоя нового кода).
- Проверить live: `/`, `/achievements`, `/leaderboard`, `/auth/login`, `/auth/update-password`, `/docs`; 404/403-страницы.
- Обновить `last_checked_commit` после пуша.

## Риски

- **Локальная сеть блэкхолит TCP-диапазоны Supabase** (`8.47.69.0/24`, `8.6.112.0/24`): локальные live-e2e невозможны, проверка — через Management API и прод. Обход: VPN/прокси.
- **Порядок миграции 013:** trigger блокирует публикацию без approved proof создателя. Новый код одобряет completion до публикации; старый код (если ещё жив на Vercel) делал наоборот. Поэтому 013 применяется только после деплоя новой версии.
- **Сброс пароля** рассчитан на implicit-flow Supabase (токены в `#access_token`). Если в проекте включат PKCE, страницу нужно доработать под `?code=`.
