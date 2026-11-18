"""Исследование BT v0.4: сравнение методов и кривая устойчивости рейтинга.

Часть A (пилотные данные research/gal_pilot_v0_3):
  - Bradley–Terry с продакшен-весами 1/(k-1) и без весов;
  - Plackett–Luce по полным личным шкалам (MM, тот же prior);
  - средняя позиция (baseline без попарной модели);
  - ранговые корреляции между методами и leave-one-scale-out прогноз
    (accuracy/Brier) для вероятностных моделей;
  - сверка реконструированного BT с прод-рейтингом (ranking.csv).

Часть B (синтетика): сколько пользователей нужно для надёжного рейтинга.
  15 достижений с известными силами, личные шкалы = истина + шум восприятия.
  Сценарии: random (каждое достижение попадает в шкалу с p=0.6), anchored
  (обязательное ядро из 5 достижений + остальные с p=0.4 — одинаковое
  ожидаемое число оценок на пользователя), sparse (p=0.25 — короткие шкалы,
  как на пилоте) и sparse_anchored (ядро из 3 + p=0.08). Метрики:
  Spearman/Kendall к истине, связность графа сравнений, top-5 overlap,
  ширина бутстрэп-ДИ.

Read-only, детерминированный seed, без сети. Запуск:
    python research/bt_study_v0_4/run_study.py
    python research/bt_study_v0_4/run_study.py --repeats 5 --bootstrap 20
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from math import exp, log
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.bradley_terry import (
    Comparison,
    build_comparisons,
    fit_bradley_terry,
    scale_comparisons,
    win_probability,
    with_bootstrap_intervals,
)
from scripts.bt_experiment import _kendall, _spearman

STUDY_DIR = Path(__file__).resolve().parent
PILOT_DIR = ROOT / "research" / "gal_pilot_v0_3"
PL_PRIOR = 0.5
_MIN_THETA = 1e-12
_MAX_THETA = 1e12


def _clamp_theta(value: float) -> float:
    return min(max(value, _MIN_THETA), _MAX_THETA)


def fit_plackett_luce(
    rankings: list[list[int]],
    *,
    prior: float = PL_PRIOR,
    max_iter: int = 500,
    tol: float = 1e-9,
) -> dict[int, float]:
    """Силы Plackett–Luce по полным ранжированиям (MM, Hunter 2004).

    rankings — списки id от самого сложного к самому лёгкому. Правдоподобие
    ранжирования: произведение theta_i / (сумма theta оставшихся). MM-шаг:
    theta_i <- (W_i + prior) / (sum_t 1/D_t + prior/(theta_i + 1)), где D_t —
    суммы theta по суффиксам ранжирований, W_i — число шкал с участием i.
    Prior и центрирование зеркалят продакшен-Bradley–Terry.
    """
    wins: dict[int, int] = defaultdict(int)
    for ranking in rankings:
        for item_id in ranking:
            wins[item_id] += 1
    ids = sorted(wins)
    theta = {item_id: 1.0 for item_id in ids}

    for _ in range(max_iter):
        denom_sum: dict[int, float] = defaultdict(float)
        for ranking in rankings:
            suffix = sum(theta[item_id] for item_id in ranking)
            for item_id in ranking:
                denom_sum[item_id] += 1.0 / max(suffix, 1e-300)
                suffix -= theta[item_id]
        updated: dict[int, float] = {}
        for item_id in ids:
            denominator = denom_sum[item_id] + prior / (theta[item_id] + 1.0)
            updated[item_id] = _clamp_theta(
                (wins[item_id] + prior) / max(denominator, 1e-300)
            )
        mean_log = sum(log(updated[item_id]) for item_id in ids) / len(ids)
        for item_id in ids:
            updated[item_id] = _clamp_theta(updated[item_id] * exp(-mean_log))
        max_delta = max(
            abs(log(updated[item_id]) - log(theta[item_id])) for item_id in ids
        )
        theta = updated
        if max_delta < tol:
            break
    return {item_id: log(value) for item_id, value in theta.items()}


def _rankings_from_scales(
    scales: dict[str, list[tuple[int, float]]],
) -> list[list[int]]:
    return [
        [item_id for item_id, _ in sorted(entries, key=lambda p: (p[1], p[0]))]
        for entries in scales.values()
        if len(entries) >= 2
    ]


def _bt_strengths(
    scales: dict[str, list[tuple[int, float]]], *, weighted: bool = True
) -> dict[int, float]:
    comparisons = build_comparisons(scales)
    if not weighted:
        comparisons = [Comparison(c.winner, c.loser, 1.0) for c in comparisons]
    ids = sorted({item_id for entries in scales.values() for item_id, _ in entries})
    fitted = fit_bradley_terry(ids, comparisons)
    return {item_id: rating.strength for item_id, rating in fitted.items()}


def _mean_position_scores(
    scales: dict[str, list[tuple[int, float]]],
) -> dict[int, float]:
    positions: dict[int, list[float]] = defaultdict(list)
    for entries in scales.values():
        for item_id, position in entries:
            positions[item_id].append(position)
    return {item_id: -mean(values) for item_id, values in positions.items()}


def _components(items: set[int], comparisons: list[Comparison]) -> int:
    parent = {item_id: item_id for item_id in items}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for comparison in comparisons:
        left, right = find(comparison.winner), find(comparison.loser)
        if left != right:
            parent[left] = right
    return len({find(item_id) for item_id in items})


def _correlation_matrix(
    methods: dict[str, dict[int, float]], items: list[int]
) -> dict[str, dict[str, float]]:
    matrix: dict[str, dict[str, float]] = {}
    for left_name, left in methods.items():
        matrix[left_name] = {}
        for right_name, right in methods.items():
            matrix[left_name][right_name] = round(
                _spearman([left[i] for i in items], [right[i] for i in items]), 4
            )
    return matrix


def load_pilot_scales() -> dict[str, list[tuple[int, float]]]:
    scales: dict[str, list[tuple[int, float]]] = defaultdict(list)
    with (PILOT_DIR / "user_scales.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["in_bt_model"] == "True" and row["record_status"] == "current":
                scales[row["user_id"]].append(
                    (int(row["achievement_id"]), float(row["position"]))
                )
    return dict(scales)


def pilot_study() -> dict:
    scales = load_pilot_scales()
    items = sorted({item_id for entries in scales.values() for item_id, _ in entries})
    rankings = _rankings_from_scales(scales)

    methods = {
        "bt_weighted": _bt_strengths(scales, weighted=True),
        "bt_unweighted": _bt_strengths(scales, weighted=False),
        "plackett_luce": fit_plackett_luce(rankings),
        "mean_position": _mean_position_scores(scales),
    }

    sanity: dict[str, float] = {}
    with (PILOT_DIR / "ranking.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["bt_strength"]:
                item_id = int(row["achievement_id"])
                sanity[str(item_id)] = round(
                    abs(methods["bt_weighted"][item_id] - float(row["bt_strength"])),
                    6,
                )

    loso: dict[str, dict] = {
        name: {
            "pairs": 0,
            "correct": 0,
            "ties": 0,
            "brier_sum": 0.0,
            "covered": 0,
            "covered_correct": 0,
            "covered_ties": 0,
            "covered_brier_sum": 0.0,
        }
        for name in ("bt_weighted", "bt_unweighted", "plackett_luce")
    }
    folds = []
    for user_id, entries in scales.items():
        if len(entries) < 2:
            continue
        train = {other: e for other, e in scales.items() if other != user_id}
        train_items = {i for e in train.values() for i, _ in e}
        train_rankings = _rankings_from_scales(train)
        fitted = {
            "bt_weighted": _bt_strengths(train, weighted=True),
            "bt_unweighted": _bt_strengths(train, weighted=False),
            "plackett_luce": fit_plackett_luce(train_rankings),
        }
        held_pairs = scale_comparisons(entries)
        fold = {"user": user_id, "scale_len": len(entries), "pairs": len(held_pairs)}
        for name, strengths in fitted.items():
            correct = ties = covered = covered_correct = covered_ties = 0
            brier_sum = covered_brier_sum = 0.0
            for pair in held_pairs:
                is_covered = pair.winner in train_items and pair.loser in train_items
                probability = win_probability(
                    strengths.get(pair.winner, 0.0), strengths.get(pair.loser, 0.0)
                )
                brier = (probability - 1.0) ** 2
                brier_sum += brier
                if probability > 0.5:
                    correct += 1
                elif probability == 0.5:
                    ties += 1
                if is_covered:
                    covered += 1
                    covered_brier_sum += brier
                    if probability > 0.5:
                        covered_correct += 1
                    elif probability == 0.5:
                        covered_ties += 1
            aggregate = loso[name]
            aggregate["pairs"] += len(held_pairs)
            aggregate["correct"] += correct
            aggregate["ties"] += ties
            aggregate["covered"] += covered
            aggregate["brier_sum"] += brier_sum
            aggregate["covered_correct"] += covered_correct
            aggregate["covered_ties"] += covered_ties
            aggregate["covered_brier_sum"] += covered_brier_sum
            fold[name] = {
                "accuracy": round((correct + 0.5 * ties) / len(held_pairs), 4),
                "brier": round(brier_sum / len(held_pairs), 4),
                "coverage": round(covered / len(held_pairs), 4),
                "accuracy_covered": (
                    round((covered_correct + 0.5 * covered_ties) / covered, 4)
                    if covered
                    else None
                ),
                "brier_covered": (
                    round(covered_brier_sum / covered, 4) if covered else None
                ),
            }
        folds.append(fold)

    loso_summary = {}
    for name, aggregate in loso.items():
        pairs = aggregate["pairs"]
        covered = aggregate["covered"]
        loso_summary[name] = {
            "pairs": pairs,
            "accuracy": round(
                (aggregate["correct"] + 0.5 * aggregate["ties"]) / pairs, 4
            ),
            "brier": round(aggregate["brier_sum"] / pairs, 4),
            "coverage": round(covered / pairs, 4),
            "accuracy_covered": round(
                (aggregate["covered_correct"] + 0.5 * aggregate["covered_ties"])
                / covered,
                4,
            ),
            "brier_covered": round(aggregate["covered_brier_sum"] / covered, 4),
        }

    return {
        "users": len(scales),
        "scale_lengths": {u: len(e) for u, e in sorted(scales.items())},
        "items": items,
        "comparisons": len(build_comparisons(scales)),
        "components": _components(set(items), build_comparisons(scales)),
        "method_strengths": {
            name: {str(i): round(values[i], 6) for i in items}
            for name, values in methods.items()
        },
        "spearman_matrix": _correlation_matrix(methods, items),
        "kendall_bt_vs_pl": round(
            _kendall(
                [methods["bt_weighted"][i] for i in items],
                [methods["plackett_luce"][i] for i in items],
            ),
            4,
        ),
        "bt_vs_prod_max_abs_diff": max(sanity.values()),
        "bt_vs_prod_per_item": sanity,
        "loso_folds": folds,
        "loso_summary": loso_summary,
    }


SCENARIOS = {
    "random": {"core": 0, "p": 0.6},
    "anchored": {"core": 5, "p": 0.4},
    "sparse": {"core": 0, "p": 0.25},
    "sparse_anchored": {"core": 3, "p": 0.08},
}


def _synthetic_replicate(
    users: int, scenario: str, rng: random.Random, bootstrap: int, noise: float
) -> dict:
    items = list(range(1, 16))
    truth = {item_id: rng.gauss(0.0, 1.0) for item_id in items}
    config = SCENARIOS[scenario]
    core = set(items[: config["core"]])
    extra_probability = config["p"]

    scales: dict[str, list[tuple[int, float]]] = {}
    for user_index in range(users):
        chosen = [
            item_id
            for item_id in items
            if item_id in core or rng.random() < extra_probability
        ]
        if len(chosen) < 2:
            continue
        perceived = {
            item_id: truth[item_id] + rng.gauss(0.0, noise) for item_id in chosen
        }
        ordered = sorted(chosen, key=lambda i: (-perceived[i], i))
        scales[f"u{user_index}"] = [
            (item_id, float(position))
            for position, item_id in enumerate(ordered, start=1)
        ]

    observed = sorted({i for entries in scales.values() for i, _ in entries})
    comparisons = build_comparisons(scales)
    methods = {
        "bt_weighted": _bt_strengths(scales, weighted=True),
        "plackett_luce": fit_plackett_luce(_rankings_from_scales(scales)),
        "mean_position": _mean_position_scores(scales),
    }
    truth_values = [truth[i] for i in observed]
    truth_top5 = set(sorted(observed, key=lambda i: -truth[i])[:5])

    result: dict = {
        "scales": len(scales),
        "evals_per_user": round(mean(len(e) for e in scales.values()), 2),
        "coverage": round(len(observed) / len(items), 4),
        "comparisons": len(comparisons),
        "components": _components(set(observed), comparisons),
    }
    for name, strengths in methods.items():
        values = [strengths[i] for i in observed]
        top5 = set(sorted(observed, key=lambda i: -strengths[i])[:5])
        result[name] = {
            "spearman": round(_spearman(truth_values, values), 4),
            "kendall": round(_kendall(truth_values, values), 4),
            "top5_overlap": round(len(top5 & truth_top5) / len(truth_top5), 4),
        }

    base = fit_bradley_terry(observed, comparisons)
    intervals = with_bootstrap_intervals(
        scales, base, replicates=bootstrap, seed=20260101
    )
    widths = [
        rating.ci_high - rating.ci_low
        for rating in intervals.values()
        if rating.ci_low is not None and rating.ci_high is not None
    ]
    result["bt_ci_width_mean"] = round(mean(widths), 2) if widths else None
    result["bt_ci_coverage"] = round(len(widths) / len(observed), 4)
    return result


def synthetic_study(
    repeats: int, bootstrap: int, seed: int, noise: float
) -> list[dict]:
    rows = []
    for scenario in SCENARIOS:
        for users in (3, 5, 8, 10, 15, 20, 30, 50):
            replicates = [
                _synthetic_replicate(
                    users,
                    scenario,
                    random.Random(f"{seed}:{scenario}:{users}:{rep}"),
                    bootstrap,
                    noise,
                )
                for rep in range(repeats)
            ]
            row: dict = {
                "scenario": scenario,
                "users": users,
                "repeats": repeats,
                "connected_frac": round(
                    sum(1 for r in replicates if r["components"] == 1) / repeats, 4
                ),
                "components_mean": round(mean(r["components"] for r in replicates), 3),
                "coverage_mean": round(mean(r["coverage"] for r in replicates), 4),
                "evals_per_user_mean": round(
                    mean(r["evals_per_user"] for r in replicates), 2
                ),
                "comparisons_mean": round(
                    mean(r["comparisons"] for r in replicates), 1
                ),
                "bt_ci_width_mean": round(
                    mean(
                        r["bt_ci_width_mean"]
                        for r in replicates
                        if r["bt_ci_width_mean"] is not None
                    ),
                    2,
                ),
                "bt_ci_coverage_mean": round(
                    mean(r["bt_ci_coverage"] for r in replicates), 4
                ),
            }
            for name in ("bt_weighted", "plackett_luce", "mean_position"):
                row[f"{name}_spearman_mean"] = round(
                    mean(r[name]["spearman"] for r in replicates), 4
                )
                row[f"{name}_spearman_min"] = round(
                    min(r[name]["spearman"] for r in replicates), 4
                )
                row[f"{name}_kendall_mean"] = round(
                    mean(r[name]["kendall"] for r in replicates), 4
                )
                row[f"{name}_top5_overlap_mean"] = round(
                    mean(r[name]["top5_overlap"] for r in replicates), 4
                )
            rows.append(row)
            print(
                f"{scenario:8s} users={users:3d} "
                f"BT rho={row['bt_weighted_spearman_mean']:.3f} "
                f"PL rho={row['plackett_luce_spearman_mean']:.3f} "
                f"avg-pos rho={row['mean_position_spearman_mean']:.3f} "
                f"connected={row['connected_frac']:.2f} "
                f"CI width={row['bt_ci_width_mean']}"
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--bootstrap", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20261118)
    parser.add_argument("--noise", type=float, default=0.5)
    args = parser.parse_args()

    pilot = pilot_study()
    (STUDY_DIR / "pilot_results.json").write_text(
        json.dumps(pilot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Pilot: {pilot['users']} users, {len(pilot['items'])} items, "
        f"{pilot['comparisons']} comparisons, components={pilot['components']}, "
        f"BT vs prod max diff={pilot['bt_vs_prod_max_abs_diff']}"
    )
    for name, summary in pilot["loso_summary"].items():
        print(f"  LOSO {name}: {summary}")

    rows = synthetic_study(args.repeats, args.bootstrap, args.seed, args.noise)
    (STUDY_DIR / "synthetic_results.json").write_text(
        json.dumps(
            {"seed": args.seed, "noise": args.noise, "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with (STUDY_DIR / "synthetic_results.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved: {STUDY_DIR / 'pilot_results.json'}, synthetic_results.json/csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
