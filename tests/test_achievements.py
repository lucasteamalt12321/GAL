from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service

client = TestClient(app)


def _sample_achievement() -> dict:
    return {
        "id": 1,
        "title": "Sample",
        "description": "d",
        "requirements": "r",
        "status": "published",
        "rank": 1,
        "average_position": 1.5,
        "evaluation_count": 3,
        "creator_id": "00000000-0000-0000-0000-000000000000",
        "category": {"id": 1, "name": "Chess", "slug": "chess"},
        "creator": {
            "id": "00000000-0000-0000-0000-000000000000",
            "username": "alice",
            "display_name": "Alice",
            "avatar_url": None,
        },
    }


def test_list_page_renders(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(achievements_service, "list_achievements", lambda **_: [])
    resp = client.get("/achievements")
    assert resp.status_code == 200
    assert "Achievements" in resp.text


def test_list_page_shows_achievement(monkeypatch) -> None:
    monkeypatch.setattr(
        achievements_service,
        "list_categories",
        lambda: [{"id": 1, "name": "Chess", "slug": "chess"}],
    )
    monkeypatch.setattr(
        achievements_service, "list_achievements", lambda **_: [_sample_achievement()]
    )
    resp = client.get("/achievements")
    assert resp.status_code == 200
    assert "Sample" in resp.text
    assert "#1" in resp.text


def test_detail_404(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: None)
    resp = client.get("/achievements/999")
    assert resp.status_code == 404


def test_detail_renders(monkeypatch) -> None:
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _sample_achievement()
    )
    resp = client.get("/achievements/1")
    assert resp.status_code == 200
    assert "Sample" in resp.text


def test_create_page_requires_auth() -> None:
    resp = client.get("/achievements/create", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_create_post_requires_auth() -> None:
    resp = client.post(
        "/achievements/create",
        data={"title": "Test"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_profile_404(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "get_profile", lambda *_: None)
    resp = client.get("/users/nobody")
    assert resp.status_code == 404


def test_profile_renders(monkeypatch) -> None:
    monkeypatch.setattr(
        achievements_service,
        "get_profile",
        lambda *_: {
            "id": "00000000-0000-0000-0000-000000000000",
            "username": "alice",
            "display_name": "Alice",
            "avatar_url": None,
            "role": "user",
        },
    )
    monkeypatch.setattr(
        achievements_service, "list_achievements_by_creator", lambda *_: []
    )
    resp = client.get("/users/alice")
    assert resp.status_code == 200
    assert "@alice" in resp.text
