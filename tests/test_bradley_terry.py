import random

from app.services.bradley_terry import (
    Comparison,
    bootstrap_rounds,
    build_comparisons,
    fit_bradley_terry,
    ordered_ids,
    win_probability,
    with_bootstrap_intervals,
)


def test_build_comparisons_orders_and_weights() -> None:
    scales = {
        "u1": [(1, 1.0), (2, 2.0), (3, 3.0)],
        "u2": [(1, 4.0)],
    }
    comparisons = build_comparisons(scales)
    pairs = {(c.winner, c.loser) for c in comparisons}
    assert pairs == {(1, 2), (1, 3), (2, 3)}
    assert all(abs(c.weight - 0.5) < 1e-12 for c in comparisons)


def test_build_comparisons_skips_short_and_tied_scales() -> None:
    comparisons = build_comparisons(
        {
            "u1": [(1, 1.0)],
            "u2": [(1, 1.0), (2, 1.0)],
        }
    )
    assert comparisons == []


def test_every_pair_is_one_contest() -> None:
    """Каждая пара личной шкалы входит в модель ровно одним соревнованием."""
    scales = {
        "u1": [(1, 1.0), (2, 2.0), (3, 3.0)],
        "u2": [(1, 1.0), (2, 2.0), (3, 3.0)],
    }
    comparisons = build_comparisons(scales)
    assert len(comparisons) == 6

    ratings = fit_bradley_terry({1, 2, 3}, comparisons)
    total_weight = sum(c.weight for c in comparisons)
    assert abs(sum(r.wins for r in ratings.values()) - total_weight) < 1e-9
    assert abs(sum(r.losses for r in ratings.values()) - total_weight) < 1e-9
    assert sum(r.comparisons for r in ratings.values()) == 2 * len(comparisons)


def _synthetic_comparisons(
    strengths: dict[int, float], games: int = 80, seed: int = 7
) -> list[Comparison]:
    rng = random.Random(seed)
    comparisons: list[Comparison] = []
    ids = sorted(strengths)
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            probability = win_probability(strengths[left], strengths[right])
            for _ in range(games):
                if rng.random() < probability:
                    comparisons.append(Comparison(left, right))
                else:
                    comparisons.append(Comparison(right, left))
    return comparisons


def test_fit_recovers_known_order() -> None:
    strengths = {1: 2.0, 2: 1.0, 3: 0.0, 4: -1.0, 5: -2.0}
    ratings = fit_bradley_terry(strengths, _synthetic_comparisons(strengths))
    assert ordered_ids(ratings) == [1, 2, 3, 4, 5]
    # Силы центрированы и близки к истинным.
    mean_strength = sum(r.strength for r in ratings.values()) / len(ratings)
    assert abs(mean_strength) < 1e-6
    assert ratings[1].strength > ratings[5].strength + 3.0


def test_disconnected_item_gets_neutral_strength() -> None:
    ratings = fit_bradley_terry(
        [1, 2, 3],
        [Comparison(1, 2)] * 5,
    )
    assert abs(ratings[3].strength) < 1e-9
    assert ratings[3].rating == 1000.0
    assert ratings[1].strength > ratings[2].strength


def test_no_comparisons_is_neutral() -> None:
    ratings = fit_bradley_terry([1, 2], [])
    assert ratings[1].strength == 0.0
    assert ratings[2].comparisons == 0


def test_rating_monotonic_with_strength() -> None:
    strengths = {1: 1.5, 2: 0.0, 3: -1.5}
    ratings = fit_bradley_terry(strengths, _synthetic_comparisons(strengths))
    assert ratings[1].rating > ratings[2].rating > ratings[3].rating
    assert abs(ratings[2].rating - 1000.0) < 100.0


def test_identical_items_have_close_strengths() -> None:
    comparisons = [Comparison(1, 2)] * 10 + [Comparison(2, 1)] * 10
    ratings = fit_bradley_terry([1, 2], comparisons)
    assert abs(ratings[1].strength - ratings[2].strength) < 1e-6


def _mixed_scales() -> dict[str, list[tuple[int, float]]]:
    return {
        "u1": [(1, 1.0), (2, 2.0), (3, 3.0)],
        "u2": [(1, 1.0), (3, 2.0), (2, 3.0)],
        "u3": [(2, 1.0), (1, 2.0), (3, 3.0)],
        "u4": [(1, 1.0), (2, 2.0), (3, 3.0)],
    }


def test_bootstrap_intervals_are_deterministic() -> None:
    scales = _mixed_scales()
    comparisons = build_comparisons(scales)
    base = fit_bradley_terry({1, 2, 3}, comparisons)

    first = with_bootstrap_intervals(scales, base, replicates=60, seed=11)
    second = with_bootstrap_intervals(scales, base, replicates=60, seed=11)

    for item_id in (1, 2, 3):
        assert first[item_id].se == second[item_id].se
        assert first[item_id].ci_low == second[item_id].ci_low
        assert first[item_id].ci_high == second[item_id].ci_high
        rating = first[item_id]
        assert rating.se is not None and rating.se > 0
        assert rating.ci_low is not None and rating.ci_high is not None
        assert rating.ci_low < rating.ci_high


def test_bootstrap_requires_enough_scales() -> None:
    scales = {
        "u1": [(1, 1.0), (2, 2.0)],
        "u2": [(1, 2.0), (2, 1.0)],
    }
    base = fit_bradley_terry({1, 2}, build_comparisons(scales))
    result = with_bootstrap_intervals(scales, base, replicates=40, seed=3)
    assert all(rating.se is None for rating in result.values())


def test_bootstrap_skips_disconnected_items() -> None:
    scales = _mixed_scales()
    base = fit_bradley_terry({1, 2, 3, 99}, build_comparisons(scales))
    result = with_bootstrap_intervals(scales, base, replicates=40, seed=5)
    assert result[99].se is None
    assert result[99].ci_low is None
    assert result[1].se is not None


def test_bootstrap_rounds_stay_within_budget() -> None:
    assert bootstrap_rounds(0, 0) == 0
    assert bootstrap_rounds(600, 200) == 200
    assert bootstrap_rounds(10_000, 200) == 20
    assert bootstrap_rounds(1, 5000) == 1000
