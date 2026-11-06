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
    ]
    calls: list[tuple[str, dict]] = []

    def _fetch_all(_client, table, _select, _order):
        return achievements if table == "achievements" else evaluations

    monkeypatch.setattr(
        bt_recompute.auth_service,
        "get_settings",
        lambda: SimpleNamespace(service_role_configured=True),
    )
    monkeypatch.setattr(
        bt_recompute.auth_service, "new_service_client", lambda: _FakeRpc(calls)
    )
    monkeypatch.setattr(bt_recompute, "_fetch_all", _fetch_all)

    summary = bt_recompute.recompute_global_ratings()
    assert summary is not None
    assert summary["items"] == 2
    assert calls and calls[0][0] == "apply_bt_ratings"
    payload = calls[0][1]["p_ratings"]
    assert {row["id"] for row in payload} == {1, 2}
    assert all("strength" in row and "rating" in row for row in payload)


def test_recompute_skips_without_service_role(monkeypatch) -> None:
    monkeypatch.setattr(
        bt_recompute.auth_service,
        "get_settings",
        lambda: SimpleNamespace(service_role_configured=False),
    )
    assert bt_recompute.recompute_global_ratings() is None


def test_try_recompute_swallows_errors(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(bt_recompute, "recompute_global_ratings", _boom)
    assert bt_recompute.try_recompute_global_ratings() is False
