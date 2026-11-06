"""Bradley–Terry ranking core (pure Python, no DB).

Идея (docs/idea.md): каждый пользователь упорядочивает выполненные достижения
по личной шкале сложности. Из порядка получаются попарные сравнения
«A сложнее B». Модель Bradley–Terry описывает вероятность такого исхода:

    P(A сложнее B) = sigmoid(s_A - s_B) = θ_A / (θ_A + θ_B)

где s_i = ln θ_i — сила сложности достижения. Силы оцениваются максимизацией
взвешенного правдоподобия регуляризованным MM-алгоритмом (Hunter, 2004):

    θ_i ← (W_i + α) / (Σ_j n_ij/(θ_i + θ_j) + α/(θ_i + 1))

W_i — взвешенные победы, n_ij — число сравнений пары, α — prior (псевдо-победы
против «среднего» достижения). Prior делает оценку конечной для изолированных
достижений и стабилизирует редкие пары. После каждой итерации силы
центрируются (среднее логарифмов = 0) — модель определена с точностью до
сдвига.

Все пары внутри одной шкалы зависимы (это одно упорядочение, а не независимые
эксперименты), поэтому вклад пользователя нормируется: каждая пара получает вес
1/(k-1), где k — число достижений в шкале. Это не устраняет зависимость
полностью, но не даёт пользователям с длинными шкалами подавлять остальных.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import exp, log

DEFAULT_PRIOR = 0.5
DEFAULT_MAX_ITER = 500
DEFAULT_TOL = 1e-9
RATING_BASE = 1000.0
# 400 очков ≈ изменение шансов в 10 раз (Elo-подобная шкала).
RATING_SCALE = 400.0 / log(10.0)
_MIN_THETA = 1e-12
_MAX_THETA = 1e12
_MIN_WEIGHT = 1e-9


@dataclass(frozen=True)
class Comparison:
    """Одно наблюдение: winner сложнее loser (weight — вес наблюдения)."""

    winner: int
    loser: int
    weight: float = 1.0


@dataclass(frozen=True)
class BTRating:
    item_id: int
    strength: float
    rating: float
    comparisons: int
    wins: float
    losses: float


def build_comparisons(
    scales: Mapping[object, Sequence[tuple[int, float]]],
) -> list[Comparison]:
    """Строит попарные сравнения из личных шкал.

    `scales`: {user_id: [(achievement_id, position), ...]}, position=1 — самое
    сложное. Внутри шкалы сравниваются все пары; вес пары = 1/(k-1).
    """
    comparisons: list[Comparison] = []
    for entries in scales.values():
        cleaned = sorted(
            ((int(item_id), float(position)) for item_id, position in entries),
            key=lambda pair: (pair[1], pair[0]),
        )
        if len(cleaned) < 2:
            continue
        weight = 1.0 / (len(cleaned) - 1)
        for i, (harder_id, harder_pos) in enumerate(cleaned):
            for easier_id, easier_pos in cleaned[i + 1 :]:
                if harder_pos == easier_pos:
                    continue
                comparisons.append(Comparison(harder_id, easier_id, weight))
    return comparisons


def fit_bradley_terry(
    item_ids: Iterable[int],
    comparisons: Iterable[Comparison],
    *,
    prior: float = DEFAULT_PRIOR,
    max_iter: int = DEFAULT_MAX_ITER,
    tol: float = DEFAULT_TOL,
) -> dict[int, BTRating]:
    """Оценивает силы Bradley–Terry регуляризованным MM-алгоритмом."""
    ids: list[int] = []
    seen: set[int] = set()
    for item_id in item_ids:
        value = int(item_id)
        if value not in seen:
            seen.add(value)
            ids.append(value)

    wins: dict[int, float] = defaultdict(float)
    losses: dict[int, float] = defaultdict(float)
    counts: dict[int, int] = defaultdict(int)
    pair_weight: dict[tuple[int, int], float] = defaultdict(float)

    for comparison in comparisons:
        winner = int(comparison.winner)
        loser = int(comparison.loser)
        weight = float(comparison.weight)
        if winner == loser or weight <= 0:
            continue
        winner, loser = (winner, loser) if winner < loser else (loser, winner)
        pair_weight[(winner, loser)] += weight
        wins[comparison.winner] += weight
        losses[comparison.loser] += weight
        counts[comparison.winner] += 1
        counts[comparison.loser] += 1
        for missing in (comparison.winner, comparison.loser):
            if int(missing) not in seen:
                seen.add(int(missing))
                ids.append(int(missing))

    adjacency: dict[int, dict[int, float]] = defaultdict(dict)
    for (left, right), weight in pair_weight.items():
        adjacency[left][right] = adjacency[left].get(right, 0.0) + weight
        adjacency[right][left] = adjacency[right].get(left, 0.0) + weight

    active = [item_id for item_id in ids if counts[item_id] > 0]
    theta = {item_id: 1.0 for item_id in ids}

    for _ in range(max_iter):
        updated: dict[int, float] = {}
        for item_id in ids:
            if counts[item_id] == 0:
                updated[item_id] = 1.0
                continue
            denominator = sum(
                weight / (theta[item_id] + theta[other])
                for other, weight in adjacency[item_id].items()
            )
            denominator += prior / (theta[item_id] + 1.0)
            value = (wins[item_id] + prior) / max(denominator, 1e-300)
            updated[item_id] = min(max(value, _MIN_THETA), _MAX_THETA)

        if active:
            mean_log = sum(log(updated[item_id]) for item_id in active) / len(active)
            for item_id in active:
                updated[item_id] = min(
                    max(updated[item_id] * exp(-mean_log), _MIN_THETA), _MAX_THETA
                )

        max_delta = max(
            (abs(log(updated[item_id]) - log(theta[item_id])) for item_id in active),
            default=0.0,
        )
        theta = updated
        if max_delta < tol:
            break

    result: dict[int, BTRating] = {}
    for item_id in ids:
        strength = log(theta[item_id]) if counts[item_id] > 0 else 0.0
        result[item_id] = BTRating(
            item_id=item_id,
            strength=strength,
            rating=RATING_BASE + RATING_SCALE * strength,
            comparisons=counts[item_id],
            wins=wins[item_id],
            losses=losses[item_id],
        )
    return result


def win_probability(strength_a: float, strength_b: float) -> float:
    """P(A сложнее B) по разности сил (для объяснений и экспериментов)."""
    delta = max(min(strength_a - strength_b, 50.0), -50.0)
    return 1.0 / (1.0 + exp(-delta))


def ordered_ids(ratings: Mapping[int, BTRating]) -> list[int]:
    """Сначала самые сложные (максимальная сила), затем id для стабильности."""
    return [
        item_id
        for item_id, _ in sorted(
            ratings.items(), key=lambda pair: (-pair[1].strength, pair[0])
        )
    ]
