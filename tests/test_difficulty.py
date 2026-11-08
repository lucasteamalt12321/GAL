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


def _scale_item(aid: int, title: str) -> dict:
    return {
        "achievement_id": aid,
        "position": 1,
        "achievement": {"id": aid, "title": title, "category": None},
    }


def test_parse_harder_count() -> None:
    assert evaluation_service.parse_harder_count("", 0) == 0
    assert evaluation_service.parse_harder_count("", 3) is None
    assert evaluation_service.parse_harder_count("2", 3) == 2
    assert evaluation_service.parse_harder_count("4", 3) is None
    assert evaluation_service.parse_harder_count("-1", 3) is None
    assert evaluation_service.parse_harder_count("abc", 3) is None


def test_create_page_asks_expected_difficulty(monkeypatch) -> None:
    _login(monkeypatch)
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(
        evaluation_service, "list_user_scale", lambda _r: [_scale_item(2, "Old one")]
    )
    resp = client.get("/achievements/create")
    assert resp.status_code == 200
    assert "Ожидаемая сложность" in resp.text
    assert "data-scale-picker" in resp.text
    assert "Old one" in resp.text


def test_create_passes_harder_count(monkeypatch) -> None:
    _login(monkeypatch)
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(
        evaluation_service, "list_user_scale", lambda _r: [_scale_item(2, "Old one")]
    )
    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: True)
    monkeypatch.setattr(
        achievements_service,
        "create_achievement",
        lambda *_a, **_k: {"id": 9},
    )
    captured: list[int | None] = []

    def _submit(*_args, **kwargs):
        captured.append(kwargs.get("harder_count"))
        return {"id": 1}

    monkeypatch.setattr(completions_service, "submit_completion", _submit)
    resp = client.post(
        "/achievements/create",
        data={
            "title": "New one",
            "external_url": "https://example.org/proof",
            "harder_count": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert captured == [1]


def test_completion_passes_harder_count(monkeypatch) -> None:
    _login(monkeypatch)
    item = {
        "id": 5,
        "title": "Existing",
        "status": "published",
        "creator_id": "33333333-3333-3333-3333-333333333333",
    }
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(
        evaluation_service, "list_user_scale", lambda _r: [_scale_item(2, "Old")]
    )
    captured: list[int | None] = []

    def _submit(*_args, **kwargs):
        captured.append(kwargs.get("harder_count"))
        return {"id": 1}

    monkeypatch.setattr(completions_service, "submit_completion", _submit)
    resp = client.post(
        "/achievements/5/complete",
        data={"external_url": "https://example.org/proof", "harder_count": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert captured == [0]


def test_completion_without_difficulty_is_rejected(monkeypatch) -> None:
    _login(monkeypatch)
    item = {
        "id": 5,
        "title": "Existing",
        "status": "published",
        "creator_id": "33333333-3333-3333-3333-333333333333",
    }
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(
        evaluation_service, "list_user_scale", lambda _r: [_scale_item(2, "Old")]
    )
    resp = client.post(
        "/achievements/5/complete",
        data={"external_url": "https://example.org/proof"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
