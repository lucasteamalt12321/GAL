-- GAL — 024: backfill достижений, уже набравших порог 2
-- 024_backfill_ranked_threshold.sql
--
-- 022 меняет порог только для будущих оценок. Достижения, получившие вторую
-- оценку до её применения, остаются в 'unknown' и не попадают в топ, пока их
-- не оценят снова. Переводим такие опубликованные достижения в 'ranked' и
-- пересчитываем ранги канонической функцией (BT + двухфазный bump).
--
-- Ранги выдаются разными значениями (max + row_number), чтобы не нарушить
-- уникальный индекс achievements_rank_unique при массовом UPDATE; затем
-- recompute_ranks_internal расставляет финальный порядок по BT.
-- Запуск от postgres (Management API): guard_ranking_fields пропускает
-- изменения только от postgres, поэтому прямой UPDATE здесь безопасен.

with to_promote as (
    select id,
           row_number() over (order by id) as rn
    from public.achievements
    where status = 'published'
      and ranking_status <> 'ranked'
      and evaluation_count >= 2
      and average_position is not null
),
base as (
    select coalesce(max(rank), 0) as max_rank
    from public.achievements
    where rank is not null
)
update public.achievements a
set ranking_status = 'ranked',
    rank_order = coalesce(a.rank_order, random()),
    rank = b.max_rank + t.rn
from to_promote t, base b
where a.id = t.id;

select public.recompute_ranks_internal();
