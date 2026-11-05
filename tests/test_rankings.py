from fastapi.testclient import TestClient

from app.main import app
from app.services import rankings as rankings_service

client = TestClient(app)


def _achievement(aid: int, rank: int, avg: float = 1.5, count: int = 3) -> dict:
    return {
        "id": aid,
        "title": f"Achievement {aid}",
        "rank": rank,
        "ranking_status": "ranked",
        "average_position": avg,
        "evaluation_count": count,
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
