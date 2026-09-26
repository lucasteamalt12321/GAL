from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import evaluation as evaluation_service

client = TestClient(app)


def _fake_user(role: str = "user") -> auth_service.CurrentUser:
    return auth_service.CurrentUser(
        id="11111111-1111-1111-1111-111111111111",
        email="user@gal.test",
        username="tester",
        display_name="Tester",
        avatar_url=None,
        role=role,
    )


def _login_as(monkeypatch, role: str = "user") -> None:
    monkeypatch.setattr(auth_service, "resolve_user", lambda _request: _fake_user(role))


def _achievement(ranking: str = "unknown") -> dict:
    return {
        "id": 1,
        "title": "Sample",
        "description": "d",
        "requirements": "r",
        "status": "published",
        "ranking_status": ranking,
        "rank": None,
        "average_position": None,
        "evaluation_count": 0,
        "creator_id": "22222222-2222-2222-2222-222222222222",
        "category": None,
        "creator": None,
    }


def test_evaluate_requires_auth() -> None:
    resp = client.get("/achievements/1/evaluate", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_evaluate_unknown_achievement_404(monkeypatch) -> None:
    _login_as(monkeypatch)
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: None)
    resp = client.get("/achievements/999/evaluate")
    assert resp.status_code == 404


def test_evaluate_redirects_when_not_allowed(monkeypatch) -> None:
    _login_as(monkeypatch)
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _achievement()
    )
    monkeypatch.setattr(
        evaluation_service,
        "evaluate_context",
        lambda *_a: (False, "Оценка зафиксирована", None),
    )
    resp = client.get("/achievements/1/evaluate", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/achievements/1?error=")


def test_evaluate_page_renders(monkeypatch) -> None:
    _login_as(monkeypatch)
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _achievement()
    )
    monkeypatch.setattr(
        evaluation_service, "evaluate_context", lambda *_a: (True, None, None)
    )
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda *_a: [])
    resp = client.get("/achievements/1/evaluate")
    assert resp.status_code == 200
    assert "Оценка сложности" in resp.text


def test_evaluate_page_shows_scale(monkeypatch) -> None:
    _login_as(monkeypatch)
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _achievement()
    )
    monkeypatch.setattr(
        evaluation_service, "evaluate_context", lambda *_a: (True, None, None)
    )
    other = {
        "achievement_id": 2,
        "position": 1,
        "achievement": {
            "id": 2,
            "title": "Other",
            "category": {"name": "Chess"},
        },
    }
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda *_a: [other])
    resp = client.get("/achievements/1/evaluate")
    assert resp.status_code == 200
    assert "Other" in resp.text
    assert '<select name="harder_count"' in resp.text


def test_evaluate_post_requires_auth() -> None:
    resp = client.post(
        "/achievements/1/evaluate",
        data={"harder_count": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_evaluate_invalid_position_redirects(monkeypatch) -> None:
    _login_as(monkeypatch)
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _achievement()
    )
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda *_a: [])
    resp = client.post(
        "/achievements/1/evaluate",
        data={"harder_count": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/achievements/1/evaluate?error=")


def test_evaluate_out_of_range_redirects(monkeypatch) -> None:
    _login_as(monkeypatch)
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _achievement()
    )
    other = {"achievement_id": 2, "position": 1, "achievement": None}
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda *_a: [other])
    resp = client.post(
        "/achievements/1/evaluate",
        data={"harder_count": "5"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/achievements/1/evaluate?error=")


def test_evaluate_success_redirects(monkeypatch) -> None:
    _login_as(monkeypatch)
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _achievement()
    )
    other = {"achievement_id": 2, "position": 1, "achievement": None}
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda *_a: [other])
    calls: list[tuple] = []

    def _fake_submit(request, achievement_id, harder_count):
        calls.append((achievement_id, harder_count))
        return {"achievement_id": 1, "my_position": 2}

    monkeypatch.setattr(evaluation_service, "submit", _fake_submit)
    resp = client.post(
        "/achievements/1/evaluate",
        data={"harder_count": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/1?info=evaluated"
    assert calls == [(1, 1)]


def test_evaluate_error_redirects(monkeypatch) -> None:
    _login_as(monkeypatch)
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _achievement()
    )
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda *_a: [])

    def _raise(*_args, **_kwargs):
        raise evaluation_service.LockedError("зафиксировано")

    monkeypatch.setattr(evaluation_service, "submit", _raise)
    resp = client.post(
        "/achievements/1/evaluate",
        data={"harder_count": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/achievements/1?error=")
