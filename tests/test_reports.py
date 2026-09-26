from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import reports as reports_service

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


def _achievement() -> dict:
    return {
        "id": 4,
        "title": "Win a tournament",
        "description": "d",
        "requirements": "r",
        "status": "published",
        "rank": 1,
        "average_position": 1.0,
        "evaluation_count": 3,
        "creator_id": "22222222-2222-2222-2222-222222222222",
        "category": None,
        "creator": None,
    }


def test_report_form_requires_auth() -> None:
    resp = client.get("/achievements/4/report", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_report_form_unknown_achievement_404(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: None)
    resp = client.get("/achievements/999/report")
    assert resp.status_code == 404


def test_report_form_renders(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _achievement()
    )
    resp = client.get("/achievements/4/report")
    assert resp.status_code == 200
    assert "Win a tournament" in resp.text
    assert 'name="reason"' in resp.text


def test_submit_report_requires_auth() -> None:
    resp = client.post(
        "/achievements/4/report",
        data={"reason": "spam"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_submit_report_success_redirects(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    calls: list[dict] = []

    def _fake(request, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(reports_service, "create_report", _fake)
    resp = client.post(
        "/achievements/4/report",
        data={"reason": "spam", "description": "Wrong description"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/4?info=report-submitted"
    assert calls[0]["achievement_id"] == 4
    assert calls[0]["reason"] == "spam"


def test_submit_report_error_redirects(monkeypatch) -> None:
    _login_as(monkeypatch, "user")

    def _raise(*_args, **_kwargs):
        raise reports_service.ReportError("Вы уже оставили жалобу")

    monkeypatch.setattr(reports_service, "create_report", _raise)
    resp = client.post(
        "/achievements/4/report",
        data={"reason": "other"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/achievements/4/report?error=")


def test_queue_requires_auth() -> None:
    resp = client.get("/reports", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_queue_forbidden_for_regular_user(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    resp = client.get("/reports")
    assert resp.status_code == 403


def test_queue_renders_pending(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    monkeypatch.setattr(
        reports_service,
        "list_pending_reports",
        lambda _r: [
            {
                "id": 1,
                "reason": "spam",
                "description": "Looks like spam",
                "status": "pending",
                "created_at": "2026-09-25T10:00:00Z",
                "achievement": {
                    "id": 4,
                    "title": "Win a tournament",
                    "status": "published",
                },
                "reporter": {"username": "alice", "display_name": "Alice"},
            }
        ],
    )
    resp = client.get("/reports")
    assert resp.status_code == 200
    assert "spam" in resp.text
    assert "Win a tournament" in resp.text
    assert "Alice" in resp.text


def test_accept_report_calls_service(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    calls: list[tuple] = []

    def _fake(request, report_id, decision, reason):
        calls.append((report_id, decision, reason))

    monkeypatch.setattr(reports_service, "decide_report", _fake)
    resp = client.post(
        "/reports/1/accept",
        data={"resolution_reason": "duplicate"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/reports"
    assert calls == [(1, "accepted", "duplicate")]


def test_reject_report_calls_service(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    calls: list[tuple] = []

    def _fake(request, report_id, decision, reason):
        calls.append((report_id, decision, reason))

    monkeypatch.setattr(reports_service, "decide_report", _fake)
    resp = client.post(
        "/reports/2/reject",
        data={"resolution_reason": "ok"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert calls == [(2, "rejected", "ok")]


def test_accept_report_forbidden_for_regular_user(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    resp = client.post("/reports/1/accept", data={})
    assert resp.status_code == 403


def test_reject_report_error_redirects(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")

    def _raise(*_args, **_kwargs):
        raise reports_service.ReportError("Жалоба не найдена")

    monkeypatch.setattr(reports_service, "decide_report", _raise)
    resp = client.post(
        "/reports/1/reject",
        data={},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/reports?error=")
