# Методология выгрузки GAL Pilot v0.3

Дата снимка: 2026-11-18, ~14:57 UTC. Источник: прод Supabase `wiwyitafoprxmlyndkwe`.
Все операции — только чтение (PostgREST GET с service-role ключом). Изменяющих
запросов, пересчётов рейтингов и обращений к `auth.*` не выполнялось.

## 1. Транспорт и воспроизводимость

- Прямой TCP/TLS к `api.supabase.com` (Management API) с этой машины сброшен
  (IP `8.47.69.1` / `8.6.112.1` блэкхолятся локальной сетью), поэтому выгрузка
  выполнена через PostgREST `https://wiwyitafoprxmlyndkwe.supabase.co/rest/v1/`
  (GET, service-role ключ из `.env`, пагинация по 1000 строк).
- Скрипт: `research/gal_pilot_v0_3/export_dataset.py`. Запуск из корня:

  ```bash
  NO_PROXY='localhost,127.0.0.1,::1' no_proxy='localhost,127.0.0.1,::1' \
      .venv/Scripts/python.exe research/gal_pilot_v0_3/export_dataset.py
  ```

  (санитизация `NO_PROXY` обязательна — иначе `httpx.InvalidURL`).
- Скрипт детерминирован: псевдонимы U1..Un назначаются сортировкой пользователей
  по (дата первой оценки, UUID). Соответствие псевдоним↔UUID нигде не сохраняется.
- Персональные данные (email, токены, IP, proof-файлы, username) не выгружались.
  Из `profiles` взят только агрегат `count(*)`.

## 2. Фактическая схема данных

- `public.evaluations` — личная шкала: одна строка на `(user_id, achievement_id)`
  (unique-констрейнт), колонки `position double precision >= 1`, `locked`,
  `created_at`, `updated_at`. **Исторических версий нет**: редактирование — upsert,
  старое значение позиции перезаписывается. `record_status=current` в выгрузке —
  константа, отражающая это отсутствие версионирования.
- `public.achievements`: `status` ∈ {pending, published, rejected, deleted}
  (скрытие = `deleted`), `ranking_status` ∈ {unknown, ranked}, `evaluation_count`,
  `average_position`, `rank`, `bt_strength`, `bt_rating`, `bt_comparisons`,
  `bt_rating_se`, `bt_rating_ci_low`, `bt_rating_ci_high`, `bt_updated_at`.
- `public.public_achievements` (вьюха, миграция 027): только `published`;
  `display_rank`, `permanent_rank`, `is_temporary`, `in_leaderboard`, `sort_strength`.
- Категории — `public.categories` (slug + русское название), FK `category_id`.

## 3. Семантика позиции (ВАЖНО: отличается от предположения в ТЗ)

**Меньшая позиция = БОЛЕЕ СЛОЖНОЕ достижение. Position 1 — самое сложное.**

Код: `app/services/bradley_terry.py:98-99` («position=1 — самое сложное»);
RPC `submit_evaluation` (миграция 029): `new_position = clamp(p_harder_count,
0, other_count) + 1`, где `p_harder_count` — сколько других достижений пользователя
сложнее данного. Шкала плотная: позиции 1..k без пропусков (сдвиги ±1 при
вставке/редактировании, миграция 029, строки 94–106).

ТЗ предполагало конвенцию «меньшая позиция = легче»; выгрузка следует реальному
коду GAL. Направление сравнений в CSV закодировано явно (см. §5), поэтому
перепроверка возможна без знания конвенции.

## 4. Преобразование шкалы в попарные сравнения (фактический код GAL)

`app/services/bradley_terry.py::scale_comparisons` (строки 75–90):

1. Записи шкалы сортируются по `(position, achievement_id)`.
2. Берутся **все уникальные пары** внутри шкалы: k(k−1)/2.
3. Пара с равными позициями (tie) пропускается (строки 87–88).
4. `winner = harder` — достижение с **меньшей** позицией (строки 85–89,
   `Comparison(harder_id, easier_id, weight)`).
5. **Вес каждой пары = 1/(k−1)** (строка 83), где k — длина шкалы. Суммарный
   вклад пользователя = k/2 (сублинейная нормировка; зависимость пар внутри
   шкалы она не устраняет — см. docstring модуля, строки 20–23).
6. Пары между шкалами разных пользователей не строятся. Дедупликации на этапе
   построения нет; агрегация весов по канонической паре происходит в
   `fit_bradley_terry` (строки 142–149).

Фильтрация (`app/services/bt_recompute.py:60-78`): в модель входят **только
оценки достижений со `status='published'`**; прочих статусов нет в evaluations
на момент выгрузки (проверено). Статуса у самих evaluations нет — все строки
действительны.

## 5. Конвенции колонок pairwise_comparisons.csv

- `first_achievement_id` / `first_position` — достижение с меньшей позицией
  (более сложное); `second_*` — с большей (более лёгкое).
- `harder_achievement_id` = `winner_achievement_id` = first;
  `easier_achievement_id` = `loser_achievement_id` = second.
- `user_id` — псевдоним автора сравнения; позиции исходные, не пересчитанные.
- `weight` = 1/(k−1) по опубликованной длине шкалы пользователя.
- Сортировка: (user_id, first_position, first_achievement_id, second_achievement_id).

## 6. Модель Bradley–Terry и рейтинг (для справки, не пересчитывался)

- MM-алгоритм (Hunter 2004): `θ_i ← (W_i + α) / (Σ_j n_ij/(θ_i+θ_j) + α/(θ_i+1))`,
  prior α = 0.5, центрирование mean(log θ) = 0, tol 1e-9, max 500 итераций.
- `bt_rating = 1000 + (400/ln 10) · s`, где s = ln θ.
- Неопределённость: кластерный бутстрэп по пользователям (ресэмплинг шкал целиком),
  seed 20260101, 200 реплик (адаптивно `min(cfg, max(20, 120000/comparisons))`),
  пороги: ≥3 шкалы, ≥5 валидных реплик, ≥2 сравнения на достижение;
  SE = (400/ln10)·stdev(s), CI = перцентили 2.5/97.5 (method 7).
- Глобальный порядок: `bt_strength desc nulls last → average_position asc →
  rank_order asc → id asc` (`app/services/ranking.py:55-67`). Порог постоянного
  ранга — **2 оценки** (`ranking_status='ranked'`); при 1 оценке — предварительная
  сила без номера («?» в UI). Временный слой топ-5 (миграция 027) на момент
  выгрузки пуст (`achievement_temporary_ratings` = 0 строк, заявок 0).
- Значения в `ranking.csv` сняты как есть из БД (последний пересчёт
  2026-11-18T06:30:53Z, новее последней оценки 2026-11-17T19:10:22Z);
  `bt_comparisons` сверен с независимо восстановленным числом пар — совпадает
  по всем 10 published-достижениям.

## 7. Граф связности

- Вершины: все 10 published-достижений (включая потенциально изолированные).
- Ребро: ≥1 попарное сравнение (неориентированно, canonical order a<b).
- Компоненты: union-find; дополнительно leave-one-out по каждому пользователю
  (`graph_analysis.json → leave_one_out`) — для анализа влияния отдельных
  пользователей на связность и идентифицируемость рейтинга.
- Межкатегорийные рёбра помечены отдельно (`cross_category_edges`).

## 8. Отличия от предварительных ориентиров ТЗ

| Ориентир ТЗ | Факт |
|---|---|
| 5 пользователей | 6 профилей, 5 с непустыми шкалами |
| 10 достижений | 15 всего (10 published, 4 pending, 1 rejected) |
| 5 достижений в шкалах | 10 (все published входят в шкалы) |
| все 5 пользователей оценивали | да, но шкала U2 длины 1 → 0 сравнений; сравнения дают 4 пользователя |
| меньшая позиция = легче | наоборот: 1 = самое сложное |