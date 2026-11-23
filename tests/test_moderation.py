from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import auth as auth_service
from app.services import bt_recompute
from app.services import completions as completions_service
from app.services import moderation as moderation_service
from app.services import top5 as top5_service

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
    item["creator_id"] = "99999999-9999-9999-9999-999999999999"
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    resp = client.post(
        "/achievements/1/complete",
        data={"external_url": "https://example.org/proof"},
    )
    assert resp.status_code == 404


def test_creator_can_attach_proof_to_pending(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    from app.services import achievements as achievements_service

    item = _published()
    item["status"] = "pending"
    item["creator_id"] = _fake_user().id
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(
        completions_service,
        "submit_completion",
        lambda *_args, **_kwargs: {"id": 9},
    )
    resp = client.post(
        "/achievements/1/complete",
        data={"external_url": "https://example.org/proof"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/1?info=submitted"


def test_complete_ranked_achievement_allowed(monkeypatch) -> None:
    _login_as(monkeypatch, "user")
    from app.services import achievements as achievements_service

    item = _published()
    item["ranking_status"] = "ranked"
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(
        completions_service,
        "submit_completion",
        lambda *_a, **_k: {"id": 10},
    )
    resp = client.post(
        "/achievements/1/complete",
        data={"external_url": "https://example.org/proof"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/1?info=submitted"


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


def test_decide_completion_passes_top5_position(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    calls: list[tuple] = []

    def _fake(request, completion_id, decision, reason, top5_position=""):
        calls.append((completion_id, decision, reason, top5_position))

    monkeypatch.setattr(moderation_service, "decide_completion", _fake)
    resp = client.post(
        "/moderation/completions/3",
        data={"decision": "approved_top5", "reason": "ok", "top5_position": "4"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/moderation"
    assert calls == [(3, "approved_top5", "ok", "4")]


def test_decide_completion_warning_redirects(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    monkeypatch.setattr(
        moderation_service,
        "decide_completion",
        lambda *_a, **_k: "Прохождение рассмотрено, но заявка в топ-5 не обновлена.",
    )
    resp = client.post(
        "/moderation/completions/3",
        data={"decision": "approved"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/moderation?error=")


def _queue_completion(with_claim: bool) -> dict:
    row = {
        "id": 3,
        "achievement_id": 5,
        "user_id": "22222222-2222-2222-2222-222222222222",
        "status": "pending",
        "created_at": "2026-11-06T10:00:00+00:00",
        "achievement": {"id": 5, "title": "Piano song"},
        "user": {"username": "tester", "display_name": "Tester"},
        "proof_views": [],
        "top5_claim": None,
    }
    if with_claim:
        row["top5_claim"] = {
            "id": 9,
            "proposed_position": 2,
            "justification": "Сложнее, чем #2",
            "status": "pending",
        }
    return row


def test_queue_shows_three_buttons_for_top5_claim(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    monkeypatch.setattr(moderation_service, "list_pending_achievements", lambda _r: [])
    monkeypatch.setattr(
        moderation_service,
        "list_pending_completions",
        lambda _r: [_queue_completion(True), {**_queue_completion(False), "id": 4}],
    )
    monkeypatch.setattr(top5_service, "try_moderation_queue", lambda _r: [])
    resp = client.get("/moderation")
    assert resp.status_code == 200
    assert "Топ-5: просит #2" in resp.text
    assert "Сложнее, чем #2" in resp.text
    assert "Обоснование сложности:" in resp.text
    assert 'name="top5_position"' in resp.text
    assert "Принять как сложное" in resp.text
    assert resp.text.count('value="approved_top5"') == 1
    assert resp.text.count('value="approved"') == 2
    assert resp.text.count('value="rejected"') == 2
    assert "Заявки в топ-5 без ожидающего прохождения" in resp.text


def test_queue_hides_top5_place_until_achievement_published(monkeypatch) -> None:
    _login_as(monkeypatch, "moderator")
    monkeypatch.setattr(moderation_service, "list_pending_achievements", lambda _r: [])
    row = _queue_completion(True)
    row["top5_claim_blocked"] = True
    monkeypatch.setattr(
        moderation_service,
        "list_pending_completions",
        lambda _r: [row],
    )
    monkeypatch.setattr(top5_service, "try_moderation_queue", lambda _r: [])
    resp = client.get("/moderation")
    assert resp.status_code == 200
    assert "Топ-5: просит #2" in resp.text
    assert "ещё не опубликовано" in resp.text
    assert 'value="approved_top5"' not in resp.text


class _DecisionQuery:
    def __init__(self, client: "_DecisionClient", name: str) -> None:
        self.client = client
        self.name = name
        self.payload: dict | None = None

    def update(self, payload):
        self.payload = payload
        return self

    def insert(self, payload):
        self.payload = payload
        return self

    def eq(self, *_args):
        return self

    def select(self, *_args):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self.name == "achievements":
            return SimpleNamespace(data={"id": 5, "status": "published"})
        if self.name == "achievement_completions":
            self.client.updates.append(self.payload)
            return SimpleNamespace(data=[{"id": 3}])
        if self.name == "moderation_reviews":
            self.client.reviews.append(self.payload)
        return SimpleNamespace(data=[self.payload])


class _DecisionClient:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.reviews: list[dict] = []
        self.rpc_calls: list[tuple] = []

    def table(self, name: str) -> _DecisionQuery:
        return _DecisionQuery(self, name)

    def rpc(self, name: str, params: dict):
        self.rpc_calls.append((name, params))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


OTHER_ID = "22222222-2222-2222-2222-222222222222"


def _claim(applicant_id: str = OTHER_ID) -> dict:
    return {
        "id": 9,
        "applicant_id": applicant_id,
        "proposed_position": 2,
        "status": "pending",
        "achievement_id": 5,
    }


def _decision_env(monkeypatch, claims: list[dict | None]):
    moderator = _fake_user("moderator")
    request = SimpleNamespace(state=SimpleNamespace(user=moderator))
    client = _DecisionClient()
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: client)
    remaining = list(claims)
    monkeypatch.setattr(
        top5_service,
        "active_claim_for_completion",
        lambda *_a: remaining.pop(0) if remaining else None,
    )
    decisions: list[tuple] = []

    def _decide(
        _request, application_id, *, decision, reason="", estimated_position=""
    ):
        decisions.append((application_id, decision, reason, estimated_position))

    monkeypatch.setattr(top5_service, "decide_application", _decide)
    monkeypatch.setattr(bt_recompute, "try_recompute_global_ratings", lambda: None)
    return request, client, decisions


def test_decide_completion_top5_uses_moderator_position(monkeypatch) -> None:
    request, client, decisions = _decision_env(monkeypatch, [_claim(), _claim()])
    warning = moderation_service.decide_completion(
        request, 3, "approved_top5", "ok", "4"
    )
    assert warning is None
    assert client.updates[0]["status"] == "approved"
    assert decisions == [(9, "approved", "ok", "4")]


def test_decide_completion_top5_defaults_to_requested_position(monkeypatch) -> None:
    request, _client, decisions = _decision_env(monkeypatch, [_claim(), _claim()])
    moderation_service.decide_completion(request, 3, "approved_top5", "", "")
    assert decisions == [(9, "approved", "", "2")]


def test_decide_completion_top5_without_claim_raises(monkeypatch) -> None:
    request, client, decisions = _decision_env(monkeypatch, [None])
    with pytest.raises(moderation_service.ModerationError) as exc:
        moderation_service.decide_completion(request, 3, "approved_top5", "", "2")
    assert "Заявка в топ-5 не найдена" in str(exc.value)
    assert client.updates == []
    assert decisions == []


def test_decide_completion_top5_own_claim_raises(monkeypatch) -> None:
    moderator = _fake_user("moderator")
    request, client, decisions = _decision_env(
        monkeypatch, [_claim(moderator.id), _claim(moderator.id)]
    )
    with pytest.raises(moderation_service.ModerationError) as exc:
        moderation_service.decide_completion(request, 3, "approved_top5", "", "2")
    assert "собственной заявке" in str(exc.value)
    assert client.updates == []
    assert decisions == []


def test_decide_completion_top5_invalid_position_raises(monkeypatch) -> None:
    request, client, _decisions = _decision_env(monkeypatch, [_claim()])
    with pytest.raises(moderation_service.ModerationError) as exc:
        moderation_service.decide_completion(request, 3, "approved_top5", "", "9")
    assert "от 1 до 6" in str(exc.value)
    assert client.updates == []


def test_decide_completion_approved_rejects_claim(monkeypatch) -> None:
    request, client, decisions = _decision_env(monkeypatch, [_claim(), _claim()])
    warning = moderation_service.decide_completion(request, 3, "approved", "", "")
    assert warning is None
    assert client.updates[0]["status"] == "approved"
    assert decisions == [(9, "rejected", "Прохождение принято без места в топ-5.", "")]


def test_decide_completion_rejected_closes_claim(monkeypatch) -> None:
    request, client, decisions = _decision_env(monkeypatch, [_claim(), _claim()])
    moderation_service.decide_completion(request, 3, "rejected", "плохо", "")
    assert client.updates[0]["status"] == "rejected"
    assert decisions == [(9, "rejected", "плохо", "")]


def test_decide_completion_converted_claim_skips_close(monkeypatch) -> None:
    # Третья оценка при approve уже конвертировала заявку — закрывать нечего.
    request, _client, decisions = _decision_env(monkeypatch, [_claim()])
    warning = moderation_service.decide_completion(request, 3, "approved", "", "")
    assert warning is None
    assert decisions == []


def test_decide_completion_claim_failure_returns_warning(monkeypatch) -> None:
    request, client, _decisions = _decision_env(monkeypatch, [_claim(), _claim()])

    def _raise(*_args, **_kwargs):
        raise top5_service.Top5Error("Заявка уже закрыта.")

    monkeypatch.setattr(top5_service, "decide_application", _raise)
    warning = moderation_service.decide_completion(request, 3, "approved", "", "")
    assert warning is not None
    assert warning.startswith("Прохождение рассмотрено")
    assert client.updates[0]["status"] == "approved"


def test_decide_completion_plain_approve_without_claim(monkeypatch) -> None:
    request, client, decisions = _decision_env(monkeypatch, [None])
    warning = moderation_service.decide_completion(request, 3, "approved", "", "")
    assert warning is None
    assert client.updates[0]["status"] == "approved"
    assert decisions == []
