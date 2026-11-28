"""GAL ranking engine — pure, unit-testable core.

Глобальный ранг достижения определяется силой Bradley–Terry (чем выше сила,
тем сложнее). Порядок:
1. bt_strength desc (None — в конце, fallback на среднюю позицию)
2. average_position asc
3. rank_order asc    (фиксированный при переходе в 'ranked' ти-брейкер)
4. id asc

Производное в БД хранится в `achievements.rank`; эта логика повторяет
`public.recompute_ranks_internal()`. Сами силы считает
`app/services/bradley_terry.py`, пересчёт применяется RPC `apply_bt_ratings`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class RankCandidate:
    id: int
    average_position: float
    rank_order: float = 0.0
    bt_strength: float | None = None


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
        bt = row.get("bt_strength")
        candidates.append(
            RankCandidate(
                id=int(row["id"]),
                average_position=float(avg),
                rank_order=float(row.get("rank_order") or 0.0),
                bt_strength=float(bt) if bt is not None else None,
            )
        )
    return candidates


def compute_ranks(candidates: Iterable[RankCandidate]) -> dict[int, int]:
    """Возвращает {id: rank}; лучшая (наибольшая BT-сила) = ранг 1."""
    ordered = sorted(
        candidates,
        key=lambda c: (
            c.bt_strength is None,
            -(c.bt_strength or 0.0),
            c.average_position,
            c.rank_order,
            c.id,
        ),
    )
    return {c.id: i + 1 for i, c in enumerate(ordered)}


def player_score(bt_rating: float | None, max_bt_rating: float | None) -> float:
    """Очки за достижение относительно лучшего BT-рейтинга.

    Максимум даёт 1000 очков; разница в 400 рейтинга соответствует
    десятикратной разнице в награде. Без рейтинга достижение не даёт очков.
    """
    if bt_rating is None or max_bt_rating is None:
        return 0.0
    return round(1000.0 * 10 ** ((float(bt_rating) - float(max_bt_rating)) / 400.0), 2)
