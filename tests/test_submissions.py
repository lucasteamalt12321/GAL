from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service

client = TestClient(app)

USER = auth_service.CurrentUser(
    id="22222222-2222-2222-2222-222222222222",
    email="user@gal.test",
    username="tester",
    display_name="Tester",
    avatar_url=None,
    role="user",
)


def _login(monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "resolve_user", lambda _request: USER)


def _row(status: str = "rejected") -> dict:
    return {
        "id": 11,
        "achievement_id": 5,
        "status": status,
        "created_at": "2026-11-06T10:00:00+00:00",
        "approved_at": None,
        "achievement": {
            "id": 5,
            "title": "Piano song",
            "status": "published",
            "category": {"name": "Music"},
        },
        "proofs": [{"id": 1, "proof_type": "link"}],
        "proof_count": 1,
        "rejection_reason": "Плохое качество видео",
    }


def test_submissions_requires_auth() -> None:
    resp = client.get("/submissions", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_submissions_renders_and_shows_reason(monkeypatch) -> None:
    _login(monkeypatch)
    monkeypatch.setattr(
        completions_service, "list_user_completions", lambda _r: [_row()]
    )
    resp = client.get("/submissions")
    assert resp.status_code == 200
    assert "Мои заявки" in resp.text
    assert "Piano song" in resp.text
    assert "Плохое качество видео" in resp.text
    assert "/achievements/5#proof" in resp.text


def test_submissions_filter(monkeypatch) -> None:
    _login(monkeypatch)
    rows = [_row("rejected"), {**_row("approved"), "id": 12}]
    monkeypatch.setattr(completions_service, "list_user_completions", lambda _r: rows)
    resp = client.get("/submissions?status=rejected")
    assert resp.status_code == 200
    assert "Piano song" in resp.text
    # Обе строки имеют одинаковый achievement title, поэтому проверяем
    # количество ссылок «исправить»: одна отклонённая.
    assert resp.text.count("Исправить и отправить снова") == 1


def test_rejected_detail_shows_resubmit_form(monkeypatch) -> None:
    _login(monkeypatch)
    item = {
        "id": 5,
        "title": "Piano song",
        "description": "",
        "requirements": "",
        "status": "published",
        "rank": None,
        "average_position": 1.0,
        "evaluation_count": 1,
        "creator_id": "33333333-3333-3333-3333-333333333333",
        "category": None,
        "creator": None,
    }
    completion = {
        "id": 11,
        "achievement_id": 5,
        "user_id": USER.id,
        "status": "rejected",
        "created_at": "2026-11-06T10:00:00+00:00",
        "approved_at": None,
    }
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(
        completions_service, "get_user_completion", lambda *_: completion
    )
    monkeypatch.setattr(completions_service, "list_proofs", lambda *_: [])
    monkeypatch.setattr(
        completions_service,
        "get_rejection_reason",
        lambda *_: "Плохое качество видео",
    )
    monkeypatch.setattr(
        evaluation_service, "evaluate_context", lambda *_a, **_k: (False, None, None)
    )
    resp = client.get("/achievements/5")
    assert resp.status_code == 200
    assert "Заявка отклонена" in resp.text
    assert "Плохое качество видео" in resp.text
    assert "Отправить снова" in resp.text
