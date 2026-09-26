from app.services.ranking import (
    RankCandidate,
    compute_ranks,
    eligible_for_ranking,
    player_score,
)


def _row(
    aid: int,
    avg: float | None = 1.0,
    rank_order: float = 0.0,
    status: str = "published",
    ranking: str = "ranked",
) -> dict:
    return {
        "id": aid,
        "status": status,
        "ranking_status": ranking,
        "average_position": avg,
        "rank_order": rank_order,
    }


# --- compute_ranks -----------------------------------------------------------


def test_compute_ranks_descending_difficulty() -> None:
    candidates = [
        RankCandidate(id=1, average_position=2.5),
        RankCandidate(id=2, average_position=1.0),
        RankCandidate(id=3, average_position=1.5),
    ]
    assert compute_ranks(candidates) == {2: 1, 3: 2, 1: 3}


def test_compute_ranks_ties_broken_by_rank_order() -> None:
    candidates = [
        RankCandidate(id=1, average_position=1.0, rank_order=0.5),
        RankCandidate(id=2, average_position=1.0, rank_order=0.1),
    ]
    assert compute_ranks(candidates) == {2: 1, 1: 2}


def test_compute_ranks_ties_broken_by_id() -> None:
    candidates = [
        RankCandidate(id=3, average_position=1.0, rank_order=0.0),
        RankCandidate(id=2, average_position=1.0, rank_order=0.0),
    ]
    assert compute_ranks(candidates) == {2: 1, 3: 2}


def test_compute_ranks_empty() -> None:
    assert compute_ranks([]) == {}


def test_compute_ranks_single() -> None:
    assert compute_ranks([RankCandidate(id=7, average_position=3.0)]) == {7: 1}


# --- eligible_for_ranking ----------------------------------------------------


def test_eligible_filters_non_published() -> None:
    rows = [_row(1, status="pending"), _row(2, status="rejected"), _row(3)]
    assert [c.id for c in eligible_for_ranking(rows)] == [3]


def test_eligible_filters_non_ranked() -> None:
    rows = [_row(1, ranking="unknown"), _row(2)]
    assert [c.id for c in eligible_for_ranking(rows)] == [2]


def test_eligible_skips_missing_position() -> None:
    rows = [_row(1, avg=None), _row(2)]
    assert [c.id for c in eligible_for_ranking(rows)] == [2]


def test_eligible_keeps_order_and_fields() -> None:
    rows = [_row(1, avg=2.0, rank_order=0.9), _row(2, avg=1.0, rank_order=0.1)]
    got = eligible_for_ranking(rows)
    assert got[0] == RankCandidate(id=1, average_position=2.0, rank_order=0.9)
    assert got[1] == RankCandidate(id=2, average_position=1.0, rank_order=0.1)


# --- player_score ------------------------------------------------------------


def test_player_score_rank_1() -> None:
    assert player_score(1) == 1000.0


def test_player_score_rank_2() -> None:
    assert player_score(2) == 500.0


def test_player_score_rank_3() -> None:
    assert player_score(3) == 333.33


def test_player_score_none_is_zero() -> None:
    assert player_score(None) == 0.0


def test_player_score_zero_or_negative_is_zero() -> None:
    assert player_score(0) == 0.0
    assert player_score(-5) == 0.0
