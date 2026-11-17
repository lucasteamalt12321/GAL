from urllib.parse import unquote

from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service
from app.services import top5 as top5_service

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
    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: True)
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


def _completion_post(monkeypatch, creator_id: str, data: dict) -> dict:
    _login(monkeypatch)
    item = {
        "id": 5,
        "title": "Existing",
        "status": "published",
        "creator_id": creator_id,
    }
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(
        evaluation_service, "list_user_scale", lambda _r: [_scale_item(2, "Old")]
    )
    captured: dict = {}

    def _submit(*_args, **kwargs):
        captured.update(kwargs)
        return {"id": 1}

    monkeypatch.setattr(completions_service, "submit_completion", _submit)
    resp = client.post(
        "/achievements/5/complete",
        data={"external_url": "https://example.org/proof", "harder_count": "0", **data},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return captured


def test_completion_hides_proof_when_requested(monkeypatch) -> None:
    captured = _completion_post(
        monkeypatch,
        "33333333-3333-3333-3333-333333333333",
        {"hide_proofs": "on"},
    )

    assert captured["proofs_visible"] is False


def test_completion_shows_proof_by_default(monkeypatch) -> None:
    captured = _completion_post(monkeypatch, "33333333-3333-3333-3333-333333333333", {})

    assert captured["proofs_visible"] is True


def test_creator_completion_ignores_hide_request(monkeypatch) -> None:
    captured = _completion_post(monkeypatch, USER.id, {"hide_proofs": "on"})

    assert captured["proofs_visible"] is True


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


def _top5_completion_post(monkeypatch, data: dict, *, claim_exc=None):
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
    completions: list[dict] = []

    def _submit(*_args, **kwargs):
        completions.append(kwargs)
        return {"id": 7}

    monkeypatch.setattr(completions_service, "submit_completion", _submit)
    claims: list[tuple] = []

    def _claim(_request, achievement_id, **kwargs):
        claims.append((achievement_id, kwargs))
        if claim_exc is not None:
            raise claim_exc
        return {"id": 10}

    monkeypatch.setattr(top5_service, "submit_application", _claim)
    resp = client.post(
        "/achievements/5/complete",
        data={"external_url": "https://example.org/proof", "harder_count": "0", **data},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return resp.headers["location"], completions, claims


def test_completion_with_top5_claim_submits_application(monkeypatch) -> None:
    location, completions, claims = _top5_completion_post(
        monkeypatch,
        {
            "top5_claim": "on",
            "top5_position": "2",
            "top5_justification": "Сложнее, чем #2",
        },
    )
    assert location == "/achievements/5?info=submitted_top5"
    assert len(completions) == 1
    assert claims == [
        (
            5,
            {
                "proposed_position": 2,
                "justification": "Сложнее, чем #2",
                "completion_id": 7,
            },
        )
    ]


def test_completion_without_claim_skips_application(monkeypatch) -> None:
    location, completions, claims = _top5_completion_post(monkeypatch, {})
    assert location == "/achievements/5?info=submitted"
    assert len(completions) == 1
    assert claims == []


def test_top5_claim_requires_position(monkeypatch) -> None:
    location, completions, claims = _top5_completion_post(
        monkeypatch,
        {"top5_claim": "on", "top5_position": "9", "top5_justification": "Сложно"},
    )
    assert "error=" in location
    assert "место" in unquote(location)
    assert completions == []
    assert claims == []


def test_top5_claim_requires_justification(monkeypatch) -> None:
    location, completions, claims = _top5_completion_post(
        monkeypatch,
        {"top5_claim": "on", "top5_position": "2", "top5_justification": "  "},
    )
    assert "error=" in location
    assert "Обоснование сложности обязательно" in unquote(location)
    assert completions == []
    assert claims == []


def test_top5_claim_rejects_long_justification(monkeypatch) -> None:
    location, completions, claims = _top5_completion_post(
        monkeypatch,
        {
            "top5_claim": "on",
            "top5_position": "2",
            "top5_justification": "x" * (top5_service.MAX_JUSTIFICATION + 1),
        },
    )
    assert "error=" in location
    assert "слишком длинное" in unquote(location)
    assert completions == []
    assert claims == []


def test_top5_claim_failure_keeps_completion(monkeypatch) -> None:
    location, completions, claims = _top5_completion_post(
        monkeypatch,
        {"top5_claim": "on", "top5_position": "2", "top5_justification": "Сложно"},
        claim_exc=top5_service.Top5Error("Заявка уже рассматривается."),
    )
    assert location.startswith("/achievements/5?info=submitted&error=")
    assert "Заявка в топ-5 не создана: Заявка уже рассматривается." in unquote(location)
    assert len(completions) == 1
    assert len(claims) == 1
