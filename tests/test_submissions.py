from types import SimpleNamespace

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
        "proof_views": [
            {"id": 1, "proof_type": "link", "url": "https://example.org/video"}
        ],
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
    assert 'href="https://example.org/video"' in resp.text
    assert "Ссылка" in resp.text


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


def test_detail_shows_submit_form_for_logged_in_user(monkeypatch) -> None:
    _login(monkeypatch)
    item = {
        "id": 5,
        "title": "Piano song",
        "description": "",
        "requirements": "",
        "status": "published",
        "rank": 3,
        "average_position": 1.0,
        "evaluation_count": 2,
        "creator_id": "33333333-3333-3333-3333-333333333333",
        "category": None,
        "creator": None,
    }
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(completions_service, "get_user_completion", lambda *_: None)
    monkeypatch.setattr(
        evaluation_service, "evaluate_context", lambda *_a, **_k: (False, None, None)
    )
    resp = client.get("/achievements/5")
    assert resp.status_code == 200
    assert "Отправить прохождение" in resp.text
    assert 'action="/achievements/5/complete"' in resp.text


def test_detail_offers_to_hide_proof_for_regular_user(monkeypatch) -> None:
    _login(monkeypatch)
    item = {
        "id": 5,
        "title": "Piano song",
        "description": "",
        "requirements": "",
        "status": "published",
        "rank": 3,
        "average_position": 1.0,
        "evaluation_count": 2,
        "creator_id": "33333333-3333-3333-3333-333333333333",
        "category": None,
        "creator": None,
    }
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(completions_service, "get_user_completion", lambda *_: None)
    monkeypatch.setattr(
        evaluation_service, "evaluate_context", lambda *_a, **_k: (False, None, None)
    )

    resp = client.get("/achievements/5")

    assert resp.status_code == 200
    assert 'name="hide_proofs"' in resp.text
    assert "Скрыть доказательство от других пользователей" in resp.text


def test_creator_verification_proof_cannot_be_hidden(monkeypatch) -> None:
    _login(monkeypatch)
    item = {
        "id": 5,
        "title": "Piano song",
        "description": "",
        "requirements": "",
        "status": "published",
        "rank": 3,
        "average_position": 1.0,
        "evaluation_count": 2,
        "creator_id": USER.id,
        "category": None,
        "creator": None,
    }
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(completions_service, "get_user_completion", lambda *_: None)
    monkeypatch.setattr(
        evaluation_service, "evaluate_context", lambda *_a, **_k: (False, None, None)
    )

    resp = client.get("/achievements/5")

    assert resp.status_code == 200
    assert 'name="hide_proofs"' not in resp.text
    assert "Доказательство верификации создателя всегда видно" in resp.text


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


class _NoRowsQuery:
    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _NoRowsClient:
    def table(self, _name: str) -> _NoRowsQuery:
        return _NoRowsQuery()


def _detail_item(**overrides) -> dict:
    item = {
        "id": 5,
        "title": "Piano song",
        "description": "",
        "requirements": "",
        "status": "published",
        "rank": None,
        "average_position": None,
        "evaluation_count": 1,
        "creator_id": "33333333-3333-3333-3333-333333333333",
        "category": None,
        "creator": None,
    }
    item.update(overrides)
    return item


def _detail_mocks(monkeypatch, item: dict) -> None:
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(completions_service, "get_user_completion", lambda *_: None)
    monkeypatch.setattr(
        evaluation_service, "evaluate_context", lambda *_a, **_k: (False, None, None)
    )


def test_detail_shows_top5_claim_checkbox(monkeypatch) -> None:
    _login(monkeypatch)
    _detail_mocks(monkeypatch, _detail_item())
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: _NoRowsClient()
    )
    resp = client.get("/achievements/5")
    assert resp.status_code == 200
    assert 'name="top5_claim"' in resp.text
    assert "Заявка в топ-5" in resp.text
    assert 'name="top5_position"' in resp.text
    assert 'name="top5_justification"' in resp.text
    assert "Запрашиваемое место" in resp.text
    assert "Обоснование сложности" in resp.text


def test_detail_hides_top5_claim_for_permanent_top5(monkeypatch) -> None:
    _login(monkeypatch)
    _detail_mocks(monkeypatch, _detail_item(rank=3))
    resp = client.get("/achievements/5")
    assert resp.status_code == 200
    assert 'name="top5_claim"' not in resp.text


def test_detail_hides_top5_claim_after_three_evaluations(monkeypatch) -> None:
    _login(monkeypatch)
    _detail_mocks(monkeypatch, _detail_item(evaluation_count=3))
    resp = client.get("/achievements/5")
    assert resp.status_code == 200
    assert 'name="top5_claim"' not in resp.text


def test_detail_hides_top5_claim_with_active_application(monkeypatch) -> None:
    _login(monkeypatch)
    _detail_mocks(monkeypatch, _detail_item())

    class _ActiveClient(_NoRowsClient):
        def table(self, name: str):
            query = _NoRowsQuery()
            if name == "top5_applications":
                query.execute = lambda: SimpleNamespace(
                    data=[
                        {
                            "id": 9,
                            "status": "pending",
                            "proposed_position": 2,
                            "created_at": "2026-11-01T10:00:00+00:00",
                            "decision_reason": "",
                            "applicant_id": USER.id,
                        }
                    ]
                )
            return query

    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: _ActiveClient()
    )
    resp = client.get("/achievements/5")
    assert resp.status_code == 200
    assert 'name="top5_claim"' not in resp.text
