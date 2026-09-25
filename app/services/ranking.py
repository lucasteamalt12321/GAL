"""GAL ranking engine — pure, unit-testable core.

Глобальный ранг достижения: «меньше средняя позиция = сложнее». Порядок:
1. average_position asc
2. rank_order asc    (фиксированный при переходе в 'ranked' ти-брейкер)
3. id asc            (стабильность при абсолютной ничьей)

Производное в БД хранится в `achievements.rank`; эта логика повторяет
`public.recompute_ranks()`. Пересчёт выполняется атомарно внутри
`public.submit_evaluation()` при переходе в 'ranked'.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class RankCandidate:
    id: int
    average_position: float
    rank_order: float = 0.0


def eligible_for_ranking(
    rows: Iterable[Mapping], *, status: str = "published", ranking: str = "ranked"
) -> list[RankCandidate]:
    """Отбирает строки достижений, участвующие в ранкинге (как в SQL-WHERE
    `recompute_ranks`): только опубликованные и с числовой позицией."""
    candidates: list[RankCandidate] = []
    for row in rows:
        if row.get("status") != status:
            continue
        if row.get("ranking_status") != ranking:
            continue
        avg = row.get("average_position")
        if avg is None:
            continue
        candidates.append(
            RankCandidate(
                id=int(row["id"]),
                average_position=float(avg),
                rank_order=float(row.get("rank_order") or 0.0),
            )
        )
    return candidates


def compute_ranks(candidates: Iterable[RankCandidate]) -> dict[int, int]:
    """Возвращает {id: rank}; лучшая (минимальная) средняя позиция = ранг 1."""
    ordered = sorted(candidates, key=lambda c: (c.average_position, c.rank_order, c.id))
    return {c.id: i + 1 for i, c in enumerate(ordered)}


def player_score(rank: int | None) -> float:
    """Очки игрока за достижение с данным рангом: 1000 / rank.

    `unknown`/без ранга или rank <= 0 дают 0 очков (Score policy MVP).
    """
    if rank is None or rank <= 0:
        return 0.0
    return round(1000.0 / float(rank), 2)