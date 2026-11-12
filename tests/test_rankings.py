from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.main import app
from app.services import rankings as rankings_service
from app.services.rankings import leaderboard_achievements as fetch_ranked_page

client = TestClient(app)


class _FakeQuery:
    def __init__(self, fake_client: "_FakeClient") -> None:
        self.fake_client = fake_client
        self.select_arg = ""

    def select(self, select: str, **_kwargs):
        self.select_arg = select
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args):
        return self

    def range(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        self.fake_client.selects.append(self.select_arg)
        if self.select_arg == "id":
            return SimpleNamespace(data=[], count=1)
        if any(
            field in self.select_arg for field in rankings_service.UNCERTAINTY_FIELDS
        ):
            if self.fake_client.error_message is not None:
                raise APIError(
                    {
                        "message": self.fake_client.error_message,
                        "code": self.fake_client.error_code,
                        "details": None,
                        "hint": None,
                    }
                )
            return SimpleNamespace(data=[{"id": 7, "title": "With CI"}], count=None)
        return SimpleNamespace(data=[{"id": 7, "title": "Legacy"}], count=None)


class _FakeClient:
    def __init__(
        self,
        error_message: str | None = None,
        error_code: str = "PGRST204",
    ) -> None:
        self.error_message = error_message
        self.error_code = error_code
        self.selects: list[str] = []

    def table(self, _name: str) -> _FakeQuery:
        return _FakeQuery(self)


def _achievement(
    aid: int,
    rank: int,
    avg: float = 1.5,
    count: int = 3,
    *,
    rating: float | None = 1200.0,
    se: float | None = 18.0,
    ci_low: float | None = 1165.0,
    ci_high: float | None = 1235.0,
    comparisons: int = 6,
) -> dict:
    return {
        "id": aid,
        "title": f"Achievement {aid}",
        "rank": rank,
        "ranking_status": "ranked",
        "average_position": avg,
        "evaluation_count": count,
        "bt_rating": rating,
        "bt_comparisons": comparisons,
        "bt_rating_se": se,
        "bt_rating_ci_low": ci_low,
        "bt_rating_ci_high": ci_high,
        "category": {"id": 1, "name": "Chess", "slug": "chess"},
    }


def _players() -> list[dict]:
    return [
        {
            "username": "alice",
            "display_name": "Alice",
            "score": 1500.0,
            "achievement_count": 2,
        },
        {
            "username": "bob",
            "display_name": "Bob",
            "score": 500.0,
            "achievement_count": 1,
        },
    ]


def test_leaderboard_renders_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service,
        "leaderboard_achievements",
        lambda **_: ([_achievement(1, 1, 1.0, 5), _achievement(2, 2, 2.33, 3)], 2),
    )
    monkeypatch.setattr(
        rankings_service, "player_leaderboard", lambda **_: (_players(), 2)
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    body = resp.text
    assert "Рейтинг" in body
    assert "Топ достижений" in body
    assert "Achievement 1" in body
    assert "Alice" in body
    assert "1500.0" in body
    assert "1200" in body
    assert "± 18 · 95% ДИ 1165–1235" in body


def test_leaderboard_omits_interval_without_enough_data(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service,
        "leaderboard_achievements",
        lambda **_: (
            [_achievement(1, 1, se=None, ci_low=None, ci_high=None, comparisons=1)],
            1,
        ),
    )
    monkeypatch.setattr(
        rankings_service, "player_leaderboard", lambda **_: (_players(), 2)
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    assert "± " not in resp.text
    assert "1165" not in resp.text


def test_leaderboard_service_falls_back_before_migration(monkeypatch) -> None:
    fake_client = _FakeClient(
        error_message="Could not find the 'bt_rating_se' column in the schema cache"
    )
    monkeypatch.setattr(
        rankings_service.auth_service, "new_anon_client", lambda: fake_client
    )

    rows, total = fetch_ranked_page()

    assert total == 1
    assert rows == [{"id": 7, "title": "Legacy"}]
    assert fake_client.selects == [
        "id",
        rankings_service.RANKED_SELECT,
        rankings_service.LEGACY_RANKED_SELECT,
    ]


def test_leaderboard_service_keeps_new_select_after_migration(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        rankings_service.auth_service, "new_anon_client", lambda: fake_client
    )

    rows, total = fetch_ranked_page()

    assert total == 1
    assert rows == [{"id": 7, "title": "With CI"}]
    assert fake_client.selects == ["id", rankings_service.RANKED_SELECT]


def test_leaderboard_service_propagates_other_storage_errors(monkeypatch) -> None:
    fake_client = _FakeClient(
        error_message="permission denied for table achievements",
        error_code="42501",
    )
    monkeypatch.setattr(
        rankings_service.auth_service, "new_anon_client", lambda: fake_client
    )

    with pytest.raises(APIError):
        fetch_ranked_page()
    assert fake_client.selects == ["id", rankings_service.RANKED_SELECT]


def test_leaderboard_survives_missing_uncertainty_columns(monkeypatch) -> None:
    row = _achievement(1, 1)
    for key in ("bt_rating_se", "bt_rating_ci_low", "bt_rating_ci_high"):
        row.pop(key)
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([row], 1)
    )
    monkeypatch.setattr(
        rankings_service, "player_leaderboard", lambda **_: (_players(), 2)
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    assert "Achievement 1" in resp.text


def test_leaderboard_empty_state(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([], 0)
    )
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: ([], 0))
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    assert "Пока нет ранжированных достижений" in resp.text
    assert "Пока нет очков" in resp.text


def test_leaderboard_keeps_service_order(monkeypatch) -> None:
    rows = [
        {
            "username": "low",
            "display_name": "Low",
            "score": 500.0,
            "achievement_count": 1,
        },
        {
            "username": "high",
            "display_name": "High",
            "score": 2500.0,
            "achievement_count": 3,
        },
    ]
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: (rows, 2))
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([], 0)
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    body = resp.text
    assert body.index("Low") < body.index("High")


def test_leaderboard_pagination_links(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service,
        "leaderboard_achievements",
        lambda **_: ([_achievement(1, 1)], 80),
    )
    monkeypatch.setattr(
        rankings_service, "player_leaderboard", lambda **_: (_players(), 80)
    )
    resp = client.get("/leaderboard?page=2&players_page=2")
    assert resp.status_code == 200
    assert "Страница 2 из 4" in resp.text
    assert "page=3" in resp.text
    assert "players_page=3" in resp.text


def test_leaderboard_out_of_range_redirects(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([], 0)
    )
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: ([], 0))

    resp = client.get("/leaderboard?page=2", follow_redirects=False)
    assert resp.status_code == 303
    assert "page=1" in resp.headers["location"]

    resp = client.get("/leaderboard?players_page=3", follow_redirects=False)
    assert resp.status_code == 303
    assert "players_page=1" in resp.headers["location"]
