from fastapi.testclient import TestClient

from app.main import app
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import moderation as moderation_service

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


def _published() -> dict:
    return {
        "id": 1,
        "title": "Sample",
        "description": "d",
        "requirements": "r",
        "status": "published",
        "rank": None,
        "average_position": None,
        "evaluation_count": 0,
        "creator_id": "22222222-2222-2222-2222-222222222222",
        "category": None,
        "creator": None,
    }


def test_queue_requires_auth() -> None:
    resp = client.get("/moderation", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_queue_forbidden_for_regular_user(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    resp = client.get("/moderation")
    assert resp.status_code == 403


def test_queue_renders_for_moderator(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    monkeypatch.setattr(moderation_service, "list_pending_achievements", lambda _r: [])
    monkeypatch.setattr(moderation_service, "list_pending_completions", lambda _r: [])
    resp = client.get("/moderation")
    assert resp.status_code == 200
    assert 'class="moderation"' in resp.text


def test_decide_achievement_calls_service(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    calls: list[tuple] = []

    def _fake(request, achievement_id, decision, reason):
        calls.append((achievement_id, decision, reason))

    monkeypatch.setattr(moderation_service, "decide_achievement", _fake)
    resp = client.post(
        "/moderation/achievements/7",
        data={"decision": "approved", "reason": "ok"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/moderation"
    assert calls == [(7, "approved", "ok")]


def test_decide_completion_forbidden_for_regular_user(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    resp = client.post(
        "/moderation/completions/3",
        data={"decision": "approved"},
    )
    assert resp.status_code == 403


def test_decide_completion_error_redirects(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")

    def _raise(*_args, **_kwargs):
        raise moderation_service.ModerationError("уже рассмотрено")

    monkeypatch.setattr(moderation_service, "decide_completion", _raise)
    resp = client.post(
        "/moderation/completions/3",
        data={"decision": "rejected"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/moderation?error=")


def test_complete_requires_auth() -> None:
    resp = client.post(
        "/achievements/1/complete",
        data={"external_url": "https://example.org/proof"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_complete_unknown_achievement_404(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    from app.services import achievements as achievements_service

    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: None)
    resp = client.post(
        "/achievements/999/complete",
        data={"external_url": "https://example.org/proof"},
    )
    assert resp.status_code == 404


def test_complete_pending_achievement_404(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    from app.services import achievements as achievements_service

    item = _published()
    item["status"] = "pending"
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    resp = client.post(
        "/achievements/1/complete",
        data={"external_url": "https://example.org/proof"},
    )
    assert resp.status_code == 404


def test_complete_success_redirects(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    from app.services import achievements as achievements_service

    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _published()
    )
    monkeypatch.setattr(
        completions_service,
        "submit_completion",
        lambda *_args, **_kwargs: {"id": 5},
    )
    resp = client.post(
        "/achievements/1/complete",
        data={"external_url": "https://example.org/proof"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/1?info=submitted"


def test_complete_error_redirects_with_message(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    from app.services import achievements as achievements_service

    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _published()
    )

    def _raise(*_args, **_kwargs):
        raise completions_service.AlreadyCompletedError("уже отправлено")

    monkeypatch.setattr(completions_service, "submit_completion", _raise)
    resp = client.post(
        "/achievements/1/complete",
        data={"external_url": "https://example.org/proof"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/achievements/1?error=")
