"""Эксперимент: восстановление глобальной шкалы моделью Bradley–Terry.

Синтетика: генерируются достижения с известными силами, сравниваются попарно
по модели P(A>B)=sigmoid(s_A-s_B), затем MM-оценка сравнивается с истиной
(Spearman/Kendall), а bootstrap показывает устойчивость рангов.

Запуск:
    python scripts/bt_experiment.py
    python scripts/bt_experiment.py --items 30 --games 40 --bootstrap 20
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.bradley_terry import (
    Comparison,
    fit_bradley_terry,
    win_probability,
)


def _spearman(left: list[float], right: list[float]) -> float:
    n = len(left)
    if n < 2:
        return 1.0
    rank_left = _average_ranks(left)
    rank_right = _average_ranks(right)
    mean_left = mean(rank_left)
    mean_right = mean(rank_right)
    covariance = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(rank_left, rank_right)
    )
    var_left = sum((a - mean_left) ** 2 for a in rank_left)
    var_right = sum((b - mean_right) ** 2 for b in rank_right)
    if var_left == 0 or var_right == 0:
        return 0.0
    return covariance / (var_left * var_right) ** 0.5


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while (
            end + 1 < len(order) and values[order[end + 1]] == values[order[position]]
        ):
            end += 1
        average = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks


def _kendall(left: list[float], right: list[float]) -> float:
    concordant = discordant = 0
    for i in range(len(left)):
        for j in range(i + 1, len(left)):
            product = (left[i] - left[j]) * (right[i] - right[j])
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 1.0


def _simulate(
    strengths: dict[int, float], games: int, rng: random.Random
) -> list[Comparison]:
    comparisons: list[Comparison] = []
    ids = sorted(strengths)
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            probability = win_probability(strengths[left], strengths[right])
            for _ in range(games):
                if rng.random() < probability:
                    comparisons.append(Comparison(left, right))
                else:
                    comparisons.append(Comparison(right, left))
    return comparisons


def _brier(ratings: dict[int, float], comparisons: list[Comparison]) -> float:
    total = 0.0
    for comparison in comparisons:
        probability = win_probability(
            ratings[comparison.winner], ratings[comparison.loser]
        )
        total += (probability - 1.0) ** 2
    return total / len(comparisons) if comparisons else 0.0


def run(items: int, games: int, bootstrap: int, seed: int) -> dict:
    rng = random.Random(seed)
    true_strengths = {i: rng.gauss(0.0, 1.0) for i in range(1, items + 1)}
    comparisons = _simulate(true_strengths, games, rng)
    ratings = fit_bradley_terry(true_strengths, comparisons)
    estimated = {item: rating.strength for item, rating in ratings.items()}
    ids = sorted(true_strengths)
    truth = [true_strengths[item] for item in ids]
    predicted = [estimated[item] for item in ids]

    base_top = sorted(ids, key=lambda i: -estimated[i])[:10]
    stability: list[float] = []
    top_overlap: list[float] = []
    for _ in range(bootstrap):
        sample = [rng.choice(comparisons) for _ in comparisons]
        boot = fit_bradley_terry(ids, sample)
        boot_strengths = {item: rating.strength for item, rating in boot.items()}
        stability.append(_spearman(predicted, [boot_strengths[item] for item in ids]))
        boot_top = sorted(ids, key=lambda i: -boot_strengths[i])[:10]
        top_overlap.append(len(set(base_top) & set(boot_top)) / len(base_top))

    return {
        "items": items,
        "games_per_pair": games,
        "comparisons": len(comparisons),
        "spearman_vs_truth": round(_spearman(truth, predicted), 4),
        "kendall_vs_truth": round(_kendall(truth, predicted), 4),
        "brier": round(_brier(estimated, comparisons), 4),
        "bootstrap_runs": bootstrap,
        "bootstrap_spearman_mean": round(mean(stability), 4) if stability else None,
        "bootstrap_spearman_min": round(min(stability), 4) if stability else None,
        "top10_overlap_mean": round(mean(top_overlap), 4) if top_overlap else None,
        "top10_truth": [
            f"#{i}" for i in sorted(ids, key=lambda i: -true_strengths[i])[:10]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=40)
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--bootstrap", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.items < 2:
        parser.error("--items must be >= 2")

    result = run(args.items, args.games, args.bootstrap, args.seed)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Bradley–Terry experiment")
        for key, value in result.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
