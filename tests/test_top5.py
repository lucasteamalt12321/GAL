from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import auth as auth_service
from app.services import top5 as top5_service

client = TestClient(app)


def _user(role: str = "user", uid: str = "11111111-1111-1111-1111-111111111111"):
    return auth_service.CurrentUser(
        id=uid,
        email="user@gal.test",
        username="tester",
        display_name="Tester",
        avatar_url=None,
        role=role,
    )


def _login(
    monkeypatch, role: str = "user", uid: str = "11111111-1111-1111-1111-111111111111"
) -> None:
    current = _user(role, uid)
    monkeypatch.setattr(auth_service, "resolve_user", lambda _request: current)


def _achievement(**overrides) -> dict:
    base = {
        "id": 1,
        "title": "Sample",
        "status": "published",
        "creator_id": "11111111-1111-1111-1111-111111111111",
        "evaluation_count": 0,
        "rank": None,
        "permanent_rank": None,
        "category": None,
        "creator": None,
    }
    base.update(overrides)
    return base


class _Query:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def table(self, _name: str) -> _Query:
        return _Query(self.rows)


def _request(user, rows: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user=user))


def test_parse_position_bounds() -> None:
    assert top5_service.parse_position("1") == 1
    assert top5_service.parse_position("5") == 5
    assert top5_service.parse_position("6") is None
    assert top5_service.parse_position("0") is None
    assert top5_service.parse_position("²") is None
    assert top5_service.parse_position("1.0") is None


def test_parse_position_six_for_votes() -> None:
    assert top5_service.parse_position("6", maximum=6) == 6


def test_application_context_blocks_already_permanent() -> None:
    request = _request(_user())
    context = top5_service.application_context(
        request, _achievement(evaluation_count=3)
    )
    assert context["can_claim"] is False


def test_application_context_uses_permanent_rank_not_display_rank(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: _Client([])
    )
    request = _request(_user())
    context = top5_service.application_context(
        request, _achievement(rank=4, permanent_rank=8, is_temporary=True)
    )
    assert context["can_claim"] is True


def test_application_context_allows_any_user_without_active(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: _Client([])
    )
    # Не автор достижения: заявку подаёт любой пользователь с прохождением.
    other = _user(uid="22222222-2222-2222-2222-222222222222")
    context = top5_service.application_context(_request(other), _achievement())
    assert context["can_claim"] is True
    assert context["my_application"] is None


def test_application_context_blocks_when_active(monkeypatch) -> None:
    user = _user()
    monkeypatch.setattr(
        auth_service,
        "user_client_from_request",
        lambda _r: _Client([{"id": 9, "status": "approved", "applicant_id": user.id}]),
    )
    context = top5_service.application_context(_request(user), _achievement())
    assert context["can_claim"] is False
    assert context["my_application"]["id"] == 9
    assert context["is_applicant"] is True


def test_top5_page_requires_login() -> None:
    resp = client.get("/top5", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_top5_page_renders_applications(monkeypatch) -> None:
    _login(monkeypatch)
    monkeypatch.setattr(
        top5_service,
        "list_my_applications",
        lambda _r: [
            {
                "id": 3,
                "achievement_id": 1,
                "proposed_position": 2,
                "status": "approved",
                "created_at": "2026-11-01T10:00:00+00:00",
                "decision_reason": "",
                "achievement": {"id": 1, "title": "Sample"},
            }
        ],
    )
    resp = client.get("/top5")
    assert resp.status_code == 200
    assert "Временное место в топ-5" in resp.text
    assert "Sample" in resp.text
    assert "Приглашения эксперта" not in resp.text


def test_separate_apply_route_removed(monkeypatch) -> None:
    _login(monkeypatch)
    resp = client.get("/top5/apply/1", follow_redirects=False)
    assert resp.status_code == 404
    resp = client.post("/top5/apply/1", data={}, follow_redirects=False)
    assert resp.status_code == 404


def test_application_detail_renders_and_has_decision_form(monkeypatch) -> None:
    _login(monkeypatch, "moderator")
    bundle = {
        "application": {
            "id": 5,
            "status": "pending",
            "proposed_position": 2,
            "justification": "Harder than #2",
            "evidence_url": "https://example.org/run",
            "evidence_note": "",
            "created_at": "2026-11-01T10:00:00+00:00",
            "decided_at": None,
            "decision_reason": "",
            "achievement": {
                "id": 1,
                "title": "Sample",
                "evaluation_count": 1,
                "creator_id": "22222222-2222-2222-2222-222222222222",
            },
            "applicant": {"username": "tester", "display_name": "Tester"},
        },
        "evaluations": [],
        "events": [],
        "my_evaluation": None,
        "is_moderator": True,
        "can_decide": True,
        "can_cancel": False,
        "is_applicant": False,
    }
    monkeypatch.setattr(top5_service, "application_bundle", lambda *_: bundle)
    resp = client.get("/top5/5")
    assert resp.status_code == 200
    assert "Harder than #2" in resp.text
    assert "Решение модератора" in resp.text
    assert 'name="estimated_position"' in resp.text
    assert "По заявке (предложено автором)" in resp.text
    assert "Пригласить эксперта" not in resp.text
    assert "Оценка эксперта" not in resp.text
    assert 'value="expert_review"' not in resp.text


def test_moderation_decide_calls_service(monkeypatch) -> None:
    _login(monkeypatch, "moderator")
    calls: list[tuple] = []

    def _fake(request, application_id, *, decision, reason="", estimated_position=""):
        calls.append((application_id, decision, reason, estimated_position))

    monkeypatch.setattr(top5_service, "decide_application", _fake)
    resp = client.post(
        "/moderation/top5/5",
        data={"decision": "approved", "reason": "ok", "estimated_position": "2"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/top5/5?info=decided"
    assert calls == [(5, "approved", "ok", "2")]


def test_decide_rejects_expert_review(monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: object())

    with pytest.raises(top5_service.Top5Error) as exc:
        top5_service.decide_application(
            _request(_user("moderator")), 5, decision="expert_review"
        )

    assert "Недопустимое решение" in str(exc.value)


def _api_error(code: str, message: str) -> Exception:
    from postgrest.exceptions import APIError

    return APIError({"message": message, "code": code, "details": None, "hint": None})


class _FailingQuery:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

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

    def maybe_single(self):
        return self

    def execute(self):
        raise self.exc


class _FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def table(self, _name: str) -> _FailingQuery:
        return _FailingQuery(self.exc)


def test_missing_schema_degrades_lists_to_empty(monkeypatch) -> None:
    missing = _api_error(
        "PGRST205",
        "Could not find the table 'public.top5_applications' in the schema cache",
    )
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: _FailingClient(missing)
    )
    request = _request(_user())
    assert top5_service.list_my_applications(request) == []
    assert top5_service.list_evaluations(request, 5) == []
    assert top5_service.list_events(request, 5) == []
    assert top5_service.get_application(request, 5) is None


def test_bundle_hides_vote_count_from_applicant(monkeypatch) -> None:
    user = _user()
    application = {
        "id": 5,
        "status": "pending",
        "applicant_id": user.id,
        "achievement": {"creator_id": user.id},
        "proposed_position": 1,
    }
    monkeypatch.setattr(top5_service, "get_application", lambda *_: application)
    monkeypatch.setattr(top5_service, "list_evaluations", lambda *_: [])
    monkeypatch.setattr(top5_service, "list_events", lambda *_: [])

    bundle = top5_service.application_bundle(_request(user), 5)

    assert bundle["evaluations_hidden"] is True


class _ExistingEvaluationQuery:
    def __init__(self, existing: dict | None) -> None:
        self.existing = existing
        self.payload: dict | None = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def maybe_single(self):
        return self

    def upsert(self, payload, **_kwargs):
        self.payload = payload
        return self

    def execute(self):
        if self.payload is not None:
            return SimpleNamespace(data=[self.payload])
        return SimpleNamespace(data=self.existing)


class _ExistingEvaluationClient:
    def __init__(self, existing: dict | None) -> None:
        self.query = _ExistingEvaluationQuery(existing)

    def table(self, _name: str):
        return self.query


def test_save_evaluation_keeps_existing_type() -> None:
    client = _ExistingEvaluationClient({"evaluator_type": "expert"})
    application = {"id": 5}

    top5_service._save_evaluation(client, _user("moderator"), application, 2, "note")

    assert client.query.payload["evaluator_type"] == "expert"
    assert client.query.payload["estimated_position"] == 2


def test_save_evaluation_defaults_to_moderator() -> None:
    client = _ExistingEvaluationClient(None)
    application = {"id": 5}

    top5_service._save_evaluation(client, _user("moderator"), application, 3, "note")

    assert client.query.payload["evaluator_type"] == "moderator"
    assert client.query.payload["estimated_position"] == 3


class _CompletionQuery:
    def __init__(self, row: dict | None) -> None:
        self.row = row

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.row)


class _ApplicationsQuery(_Query):
    def __init__(self, rows: list[dict]) -> None:
        super().__init__(rows)
        self.inserted: dict | None = None

    def insert(self, payload):
        self.inserted = payload
        return self

    def execute(self):
        if self.inserted is not None:
            return SimpleNamespace(data=[{"id": 10, **self.inserted}])
        return SimpleNamespace(data=self.rows)


class _SubmitClient:
    def __init__(self, completion_row: dict | None, active: list[dict]) -> None:
        self.applications_query = _ApplicationsQuery(active)
        self.completion_query = _CompletionQuery(completion_row)

    def table(self, name: str):
        if name == "achievement_completions":
            return self.completion_query
        return self.applications_query


def _submit_client(monkeypatch, completion_row, active=None) -> _SubmitClient:
    fake = _SubmitClient(completion_row, active or [])
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: fake)
    monkeypatch.setattr(
        top5_service,
        "_achievement_row",
        lambda *_: _achievement(evaluation_count=1),
    )
    return fake


def test_submit_application_links_completion(monkeypatch) -> None:
    user = _user()
    fake = _submit_client(
        monkeypatch, {"id": 7, "user_id": user.id, "achievement_id": 1}
    )

    result = top5_service.submit_application(
        _request(user),
        1,
        proposed_position="2",
        justification="Harder",
        completion_id=7,
    )

    assert result["id"] == 10
    payload = fake.applications_query.inserted
    assert payload["completion_id"] == 7
    assert payload["applicant_id"] == user.id
    assert payload["proposed_position"] == 2
    assert "evidence_url" not in payload


def test_submit_application_rejects_foreign_completion(monkeypatch) -> None:
    _submit_client(
        monkeypatch,
        {
            "id": 7,
            "user_id": "22222222-2222-2222-2222-222222222222",
            "achievement_id": 1,
        },
    )

    with pytest.raises(top5_service.Top5Error) as exc:
        top5_service.submit_application(
            _request(_user()),
            1,
            proposed_position="2",
            justification="Harder",
            completion_id=7,
        )

    assert "вместе с вашим прохождением" in str(exc.value)


def test_submit_application_rejects_second_active(monkeypatch) -> None:
    user = _user()
    _submit_client(
        monkeypatch,
        {"id": 7, "user_id": user.id, "achievement_id": 1},
        active=[{"id": 9}],
    )

    with pytest.raises(top5_service.Top5Error) as exc:
        top5_service.submit_application(
            _request(user),
            1,
            proposed_position="2",
            justification="Harder",
            completion_id=7,
        )

    assert "уже рассматривается" in str(exc.value)


def test_submit_application_requires_justification(monkeypatch) -> None:
    user = _user()
    _submit_client(monkeypatch, {"id": 7, "user_id": user.id, "achievement_id": 1})

    with pytest.raises(top5_service.Top5Error) as exc:
        top5_service.submit_application(
            _request(user),
            1,
            proposed_position="2",
            justification="  ",
            completion_id=7,
        )

    assert "Обоснование" in str(exc.value)


def test_submit_application_validates_position(monkeypatch) -> None:
    user = _user()
    _submit_client(monkeypatch, {"id": 7, "user_id": user.id, "achievement_id": 1})

    with pytest.raises(top5_service.Top5Error) as exc:
        top5_service.submit_application(
            _request(user),
            1,
            proposed_position="6",
            justification="Harder",
            completion_id=7,
        )

    assert "от 1 до 5" in str(exc.value)


def test_claims_for_completions_degrades_without_migration() -> None:
    missing_column = _api_error("PGRST204", "column completion_id does not exist")
    fake = _FailingClient(missing_column)
    assert top5_service.claims_for_completions(fake, [1, 2]) == {}
    assert top5_service.active_claim_for_completion(fake, 1) is None
    missing_table = _api_error(
        "PGRST205",
        "Could not find the table 'public.top5_applications' in the schema cache",
    )
    fake = _FailingClient(missing_table)
    assert top5_service.claims_for_completions(fake, [1]) == {}
    assert top5_service.active_claim_for_completion(fake, 1) is None


def test_legacy_queue_filters_pending_completion_claims() -> None:
    class _LegacyClient:
        def table(self, name: str):
            if name == "top5_applications":
                return _Query(
                    [
                        {"id": 1, "completion_id": 7},
                        {"id": 2, "completion_id": 8},
                        {"id": 3, "completion_id": None},
                    ]
                )
            return _Query(
                [{"id": 7, "status": "pending"}, {"id": 8, "status": "approved"}]
            )

    applications = [{"id": 1}, {"id": 2}, {"id": 3}]
    legacy = top5_service._legacy_applications(_LegacyClient(), applications)
    assert [row["id"] for row in legacy] == [2, 3]


def test_legacy_queue_keeps_all_before_migration() -> None:
    missing_column = _api_error("PGRST204", "column completion_id does not exist")
    applications = [{"id": 1}, {"id": 2}]
    legacy = top5_service._legacy_applications(
        _FailingClient(missing_column), applications
    )
    assert legacy == applications


def test_moderation_queue_shows_top5(monkeypatch) -> None:
    _login(monkeypatch, "moderator")
    from app.services import moderation as moderation_service

    monkeypatch.setattr(moderation_service, "list_pending_achievements", lambda _r: [])
    monkeypatch.setattr(moderation_service, "list_pending_completions", lambda _r: [])
    monkeypatch.setattr(
        top5_service,
        "try_moderation_queue",
        lambda _r: [
            {
                "id": 5,
                "status": "pending",
                "proposed_position": 2,
                "created_at": "2026-11-01T10:00:00+00:00",
                "achievement": {"title": "Sample"},
                "applicant": {"username": "tester", "display_name": "Tester"},
                "evaluation_count": 1,
            }
        ],
    )
    resp = client.get("/moderation")
    assert resp.status_code == 200
    assert "Заявки в топ-5 (старые)" in resp.text
    assert 'href="/top5/5"' in resp.text
    assert "место выставляется вручную при решении" in resp.text
