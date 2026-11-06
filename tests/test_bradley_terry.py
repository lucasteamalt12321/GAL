import random

from app.services.bradley_terry import (
    Comparison,
    build_comparisons,
    fit_bradley_terry,
    ordered_ids,
    win_probability,
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
