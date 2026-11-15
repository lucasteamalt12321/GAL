from types import SimpleNamespace

from app.services import bt_recompute


class _FakeRpc:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.calls = calls

    def rpc(self, name: str, params: dict) -> "_FakeRpc":
        self.calls.append((name, params))
        return self

    def execute(self):
        return SimpleNamespace(data=1)


def _settings(*, configured: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        service_role_configured=configured,
        bt_bootstrap_replicates=60,
        bt_bootstrap_seed=13,
    )


def test_recompute_builds_payload_from_published(monkeypatch) -> None:
    achievements = [
        {"id": 1, "status": "published"},
        {"id": 2, "status": "published"},
        {"id": 3, "status": "pending"},
    ]
    evaluations = [
        {"achievement_id": 1, "user_id": "u1", "position": 1},
        {"achievement_id": 2, "user_id": "u1", "position": 2},
        {"achievement_id": 3, "user_id": "u1", "position": 3},
        {"achievement_id": 1, "user_id": "u2", "position": 2},
        {"achievement_id": 2, "user_id": "u2", "position": 1},
        {"achievement_id": 1, "user_id": "u3", "position": 1},
        {"achievement_id": 2, "user_id": "u3", "position": 2},
        {"achievement_id": 1, "user_id": "u4", "position": 2},
        {"achievement_id": 2, "user_id": "u4", "position": 1},
    ]
    calls: list[tuple[str, dict]] = []

    def _fetch_all(_client, table, _select, _order):
        return achievements if table == "achievements" else evaluations

    monkeypatch.setattr(
        bt_recompute.auth_service,
        "get_settings",
        lambda: _settings(),
    )
    monkeypatch.setattr(
        bt_recompute.auth_service, "new_service_client", lambda: _FakeRpc(calls)
    )
    monkeypatch.setattr(bt_recompute, "_fetch_all", _fetch_all)

    summary = bt_recompute.recompute_global_ratings()
    assert summary is not None
    assert summary["items"] == 2
    assert summary["replicates"] == 60
    assert calls and calls[0][0] == "apply_bt_ratings"
    payload = calls[0][1]["p_ratings"]
    assert {row["id"] for row in payload} == {1, 2}
    assert all(
        {"strength", "rating", "comparisons", "se", "ci_low", "ci_high"} <= set(row)
        for row in payload
    )
    assert any(row["se"] is not None and row["se"] > 0 for row in payload)
    for row in payload:
        assert row["ci_low"] <= row["ci_high"]


def test_recompute_skips_without_service_role(monkeypatch) -> None:
    monkeypatch.setattr(
        bt_recompute.auth_service,
        "get_settings",
        lambda: _settings(configured=False),
    )
    assert bt_recompute.recompute_global_ratings() is None


def test_try_recompute_swallows_errors(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(bt_recompute, "recompute_global_ratings", _boom)
    assert bt_recompute.try_recompute_global_ratings() is False


def _published_references() -> list[dict]:
    return [
        {
            "id": i,
            "status": "published",
            "ranking_status": "ranked",
            "evaluation_count": 3,
            "bt_strength": 3.0 - i * 0.2,
            "average_position": float(i),
            "rank": i,
        }
        for i in range(1, 7)
    ]


def _temporary_achievement() -> dict:
    return {
        "id": 100,
        "status": "published",
        "ranking_status": "unknown",
        "evaluation_count": 0,
        "bt_strength": None,
        "average_position": 1.0,
        "rank": None,
    }


def _patch_temporary_tables(
    monkeypatch,
    *,
    applications: list[dict],
    evaluations: list[dict],
    achievement: dict | None = None,
) -> None:
    achievements = _published_references()
    if achievement is not None:
        achievements = [*achievements, achievement]
    tables = {
        "top5_applications": applications,
        "achievements": achievements,
        "top5_evaluations": evaluations,
    }

    def _fetch(_client, table, _select, _order):
        return tables[table]

    monkeypatch.setattr(bt_recompute, "_fetch_all", _fetch)


def test_reference_key_uses_rank_order_before_id() -> None:
    rows = [
        {"id": 2, "bt_strength": 2.0, "average_position": 1.0, "rank_order": 2},
        {"id": 1, "bt_strength": 2.0, "average_position": 1.0, "rank_order": 1},
    ]
    rows.sort(key=bt_recompute._reference_key)
    assert [row["id"] for row in rows] == [1, 2]


def test_reference_key_uses_fractional_rank_order() -> None:
    # rank_order — double в [0, 1) (random()); int() обнулял tie-breaker.
    rows = [
        {"id": 2, "bt_strength": 2.0, "average_position": 1.0, "rank_order": 0.7},
        {"id": 1, "bt_strength": 2.0, "average_position": 1.0, "rank_order": 0.3},
    ]
    rows.sort(key=bt_recompute._reference_key)
    assert [row["id"] for row in rows] == [1, 2]


def test_reference_key_keeps_strength_first() -> None:
    rows = [
        {"id": 1, "bt_strength": 1.0, "average_position": 1.0, "rank_order": 1},
        {"id": 2, "bt_strength": 2.0, "average_position": 9.0, "rank_order": 9},
    ]
    rows.sort(key=bt_recompute._reference_key)
    assert [row["id"] for row in rows] == [2, 1]


def test_temporary_payload_places_voted_candidate_above_top(monkeypatch) -> None:
    _patch_temporary_tables(
        monkeypatch,
        applications=[
            {
                "id": 5,
                "achievement_id": 100,
                "status": "approved",
                "proposed_position": 1,
            }
        ],
        evaluations=[
            {
                "application_id": 5,
                "evaluator_id": "u1",
                "evaluator_type": "expert",
                "estimated_position": 1,
            }
        ],
        achievement=_temporary_achievement(),
    )

    payload = bt_recompute._temporary_payload(object())

    assert len(payload) == 1
    row = payload[0]
    assert row["achievement_id"] == 100
    assert row["application_id"] == 5
    assert row["evaluator_count"] == 1
    assert row["ordinary_evaluation_count"] == 0
    # Одна победа над #1 при сильном априори чуть поднимает кандидата:
    # сила растёт на ln(1.2) относительно опоры 2.8, а не улетает в космос.
    assert 2.8 < row["strength"] < 3.2
    assert row["rating"] > bt_recompute.bradley_terry.rating_from_strength(2.8)


def test_temporary_payload_uses_proposed_position_without_votes(monkeypatch) -> None:
    _patch_temporary_tables(
        monkeypatch,
        applications=[
            {
                "id": 5,
                "achievement_id": 100,
                "status": "approved",
                "proposed_position": 2,
            }
        ],
        evaluations=[],
        achievement=_temporary_achievement(),
    )

    payload = bt_recompute._temporary_payload(object())

    assert len(payload) == 1
    strength = payload[0]["strength"]
    assert 2.4 < strength < 3.0
    assert payload[0]["evaluator_count"] == 0


def test_temporary_payload_skips_third_evaluation(monkeypatch) -> None:
    achievement = {**_temporary_achievement(), "evaluation_count": 3}
    _patch_temporary_tables(
        monkeypatch,
        applications=[
            {
                "id": 5,
                "achievement_id": 100,
                "status": "approved",
                "proposed_position": 1,
            }
        ],
        evaluations=[],
        achievement=achievement,
    )

    assert bt_recompute._temporary_payload(object()) == []


def test_temporary_payload_skips_permanent_top5(monkeypatch) -> None:
    achievement = {
        **_temporary_achievement(),
        "evaluation_count": 2,
        "rank": 3,
        "bt_strength": 2.1,
    }
    _patch_temporary_tables(
        monkeypatch,
        applications=[
            {
                "id": 5,
                "achievement_id": 100,
                "status": "approved",
                "proposed_position": 1,
            }
        ],
        evaluations=[],
        achievement=achievement,
    )

    assert bt_recompute._temporary_payload(object()) == []


def test_anchored_strength_matches_reference_on_split() -> None:
    comparisons = [
        bt_recompute.bradley_terry.Comparison(1, 100),
        bt_recompute.bradley_terry.Comparison(100, 1),
    ]
    strength = bt_recompute.bradley_terry.anchored_strength(
        100, comparisons, {1: 1.5}, initial=1.5
    )
    assert abs(strength - 1.5) < 0.2
