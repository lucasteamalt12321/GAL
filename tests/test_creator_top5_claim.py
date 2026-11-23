"""Заявка в топ-5, поданная автором при создании достижения.

Сценарий D18 + миграция 034: заявка подаётся вместе с прохождением, доказательство
заявки — proof прохождения, экспертная оценка не участвует, решает модератор.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service
from app.services import moderation as moderation_service
from app.services import top5 as top5_service

client = TestClient(app)

AUTHOR_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _user(role: str = "user", uid: str = AUTHOR_ID):
    return auth_service.CurrentUser(
        id=uid,
        email="user@gal.test",
        username="tester",
        display_name="Tester",
        avatar_url=None,
        role=role,
    )


def _login(monkeypatch, role: str = "user", uid: str = AUTHOR_ID) -> None:
    current = _user(role, uid)
    monkeypatch.setattr(auth_service, "resolve_user", lambda _request: current)


def _request(user=None):
    return SimpleNamespace(state=SimpleNamespace(user=user or _user()))


def _prepare_create(monkeypatch) -> dict:
    """Подменяет сервисы создания достижения и возвращает журнал вызовов."""
    calls: dict = {"created": [], "completions": [], "applications": []}

    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: True)
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(
        achievements_service,
        "create_achievement",
        lambda _r, **kwargs: calls["created"].append(kwargs) or {"id": 42},
    )
    monkeypatch.setattr(
        evaluation_service,
        "list_user_scale",
        lambda _r: [{"achievement_id": 1, "position": 1}],
    )
    monkeypatch.setattr(
        completions_service,
        "submit_completion",
        lambda _r, **kwargs: calls["completions"].append(kwargs) or {"id": 7},
    )

    def _submit_application(_r, achievement_id, **kwargs):
        calls["applications"].append({"achievement_id": achievement_id, **kwargs})
        return {"id": 3}

    monkeypatch.setattr(top5_service, "submit_application", _submit_application)
    return calls


def _form(**extra) -> dict:
    data = {
        "title": "Моё достижение",
        "description": "Описание",
        "requirements": "Требования",
        "harder_count": "1",
        "external_url": "https://example.org/proof",
    }
    data.update(extra)
    return data


# +------------------- форма создания достижения -------------------+


def test_create_form_offers_top5_claim(monkeypatch) -> None:
    _login(monkeypatch)
    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: True)
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(
        evaluation_service,
        "list_user_scale",
        lambda _r: [{"achievement_id": 1, "position": 1}],
    )

    resp = client.get("/achievements/create")

    assert resp.status_code == 200
    assert 'name="top5_claim"' in resp.text
    assert 'name="top5_position"' in resp.text
    assert 'name="top5_justification"' in resp.text
    # Экспертная оценка в сценарии не участвует.
    assert "эксперт" not in resp.text.lower()


# +------------------- подача заявки при создании -------------------+


def test_create_with_top5_claim_creates_application(monkeypatch) -> None:
    _login(monkeypatch)
    calls = _prepare_create(monkeypatch)

    resp = client.post(
        "/achievements/create",
        data=_form(
            top5_claim="on",
            top5_position="2",
            top5_justification="Сложнее большинства достижений",
        ),
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/42?info=submitted_top5"
    assert calls["created"], "достижение должно быть создано"
    assert calls["completions"], "прохождение создателя должно быть отправлено"
    assert calls["applications"] == [
        {
            "achievement_id": 42,
            "proposed_position": 2,
            "justification": "Сложнее большинства достижений",
            "completion_id": 7,
        }
    ]


def test_create_without_top5_claim_skips_application(monkeypatch) -> None:
    _login(monkeypatch)
    calls = _prepare_create(monkeypatch)

    resp = client.post(
        "/achievements/create",
        data=_form(),
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/42?info=submitted"
    assert calls["applications"] == []


def test_create_claim_requires_justification(monkeypatch) -> None:
    _login(monkeypatch)
    calls = _prepare_create(monkeypatch)

    resp = client.post(
        "/achievements/create",
        data=_form(top5_claim="on", top5_position="2", top5_justification="  "),
    )

    assert resp.status_code == 200
    assert "Обоснование сложности обязательно" in resp.text
    assert calls["created"] == [], "достижение не должно создаваться"
    assert calls["applications"] == []


def test_create_claim_rejects_position_out_of_range(monkeypatch) -> None:
    _login(monkeypatch)
    calls = _prepare_create(monkeypatch)

    resp = client.post(
        "/achievements/create",
        data=_form(
            top5_claim="on",
            top5_position="9",
            top5_justification="Сложнее",
        ),
    )

    assert resp.status_code == 200
    assert "Укажите запрашиваемое место в топ-5 от 1 до 5" in resp.text
    assert calls["created"] == []
    assert calls["applications"] == []


def test_create_claim_error_keeps_entered_values(monkeypatch) -> None:
    _login(monkeypatch)
    _prepare_create(monkeypatch)

    resp = client.post(
        "/achievements/create",
        data=_form(
            top5_claim="on",
            top5_position="3",
            top5_justification="",
        ),
    )

    assert resp.status_code == 200
    assert 'name="top5_claim" value="on" data-top5-claim-toggle checked' in resp.text
    assert '<option value="3" selected>' in resp.text


def test_create_claim_failure_does_not_block_achievement(monkeypatch) -> None:
    _login(monkeypatch)
    calls = _prepare_create(monkeypatch)

    def _fail(*_args, **_kwargs):
        raise top5_service.Top5Error("Заявка уже рассматривается.")

    monkeypatch.setattr(top5_service, "submit_application", _fail)

    resp = client.post(
        "/achievements/create",
        data=_form(
            top5_claim="on",
            top5_position="2",
            top5_justification="Сложнее",
        ),
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/achievements/42?info=submitted")
    assert "error=" in resp.headers["location"]
    assert calls["created"], "достижение должно быть создано несмотря на сбой заявки"


# +------------------- сервисный уровень -------------------+


class _Query:
    def __init__(self, rows: list[dict], single: bool = False) -> None:
        self.rows = rows
        self.single = single
        self.inserted: dict | None = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def order(self, *_args):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def insert(self, payload: dict):
        self.inserted = payload
        return self

    def execute(self):
        if self.inserted is not None:
            return SimpleNamespace(data=[{"id": 3, **self.inserted}])
        if self.single:
            return SimpleNamespace(data=self.rows[0] if self.rows else None)
        return SimpleNamespace(data=self.rows)


class _Client:
    """Клиент, отдающий заранее заданные строки по имени таблицы."""

    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables
        self.inserted: list[dict] = []

    def table(self, name: str):
        return _Query(self.tables.get(name, []))


def _pending_achievement(creator_id: str = AUTHOR_ID) -> dict:
    return {
        "id": 42,
        "title": "Моё достижение",
        "creator_id": creator_id,
        "status": "pending",
        "rank": None,
        "evaluation_count": 0,
        "bt_rating": None,
        "bt_strength": None,
        "ranking_status": "unknown",
    }


def test_submit_application_allows_creator_on_pending_achievement(monkeypatch) -> None:
    client_fake = _Client(
        {
            "achievements": [_pending_achievement()],
            "achievement_completions": [
                {"id": 7, "user_id": AUTHOR_ID, "achievement_id": 42}
            ],
            "top5_applications": [],
        }
    )
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: client_fake
    )

    result = top5_service.submit_application(
        _request(),
        42,
        proposed_position="2",
        justification="Сложнее",
        completion_id=7,
    )

    assert result["achievement_id"] == 42
    assert result["applicant_id"] == AUTHOR_ID
    assert result["proposed_position"] == 2
    assert result["justification"] == "Сложнее"
    # Заявка привязана к прохождению автора — это её доказательство.
    assert result["completion_id"] == 7


def test_submit_application_rejects_other_user_on_pending_achievement(
    monkeypatch,
) -> None:
    client_fake = _Client(
        {
            "achievements": [_pending_achievement()],
            "achievement_completions": [
                {"id": 7, "user_id": OTHER_ID, "achievement_id": 42}
            ],
            "top5_applications": [],
        }
    )
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: client_fake
    )

    with pytest.raises(top5_service.Top5Error):
        top5_service.submit_application(
            _request(_user(uid=OTHER_ID)),
            42,
            proposed_position="2",
            justification="Сложнее",
            completion_id=7,
        )


def test_application_context_shows_creator_claim_for_pending_achievement(
    monkeypatch,
) -> None:
    client_fake = _Client(
        {
            "top5_applications": [
                {
                    "id": 3,
                    "status": "pending",
                    "proposed_position": 2,
                    "created_at": "2026-11-23T10:00:00+00:00",
                    "decision_reason": "",
                    "applicant_id": AUTHOR_ID,
                }
            ]
        }
    )
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: client_fake
    )

    context = top5_service.application_context(_request(), _pending_achievement())

    assert context["my_application"]["id"] == 3
    assert context["is_applicant"] is True
    # Повторно отметить заявку нельзя — активная заявка уже есть.
    assert context["can_claim"] is False


def test_application_context_hides_pending_claim_from_other_user(monkeypatch) -> None:
    client_fake = _Client(
        {
            "top5_applications": [
                {
                    "id": 3,
                    "status": "pending",
                    "proposed_position": 2,
                    "created_at": "2026-11-23T10:00:00+00:00",
                    "decision_reason": "",
                    "applicant_id": AUTHOR_ID,
                }
            ]
        }
    )
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: client_fake
    )

    context = top5_service.application_context(
        _request(_user(uid=OTHER_ID)), _pending_achievement()
    )

    assert context["my_application"] is None
    assert context["can_claim"] is False


# +------------------- модерация -------------------+


def test_decide_completion_blocks_top5_before_publication(monkeypatch) -> None:
    class _AchievementQuery:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return SimpleNamespace(data={"id": 42, "status": "pending"})

    class _Client:
        def table(self, name: str):
            return _AchievementQuery()

    moderator = _user(role="moderator")
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: _Client())
    monkeypatch.setattr(
        top5_service,
        "active_claim_for_completion",
        lambda *_a: {
            "id": 9,
            "applicant_id": OTHER_ID,
            "proposed_position": 2,
            "status": "pending",
            "achievement_id": 42,
        },
    )

    with pytest.raises(moderation_service.ModerationError):
        moderation_service.decide_completion(
            _request(moderator), 7, "approved_top5", "ok", "2"
        )


def test_rejected_achievement_closes_top5_claim(monkeypatch) -> None:
    class _Query:
        def __init__(self, name: str) -> None:
            self.name = name

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def in_(self, *_args):
            return self

        def maybe_single(self):
            return self

        def update(self, _payload):
            return self

        def insert(self, _payload):
            return self

        def execute(self):
            if self.name == "achievements":
                return SimpleNamespace(
                    data={"creator_id": AUTHOR_ID, "status": "pending"}
                )
            return SimpleNamespace(data=[{"id": 42}])

    class _Client:
        def table(self, name: str) -> _Query:
            return _Query(name)

    closed: list[tuple] = []
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: _Client())
    monkeypatch.setattr(
        moderation_service,
        "_approve_creator_completion",
        lambda *_a: 7,
    )
    monkeypatch.setattr(
        moderation_service,
        "_reject_creator_completion",
        lambda *_a: None,
    )
    monkeypatch.setattr(
        moderation_service.bt_recompute,
        "try_recompute_global_ratings",
        lambda: None,
    )
    monkeypatch.setattr(
        top5_service,
        "close_claims_for_achievement",
        lambda _c, achievement_id, reason: closed.append((achievement_id, reason)),
    )

    moderation_service.decide_achievement(
        _request(_user(role="moderator")), 42, "rejected", "Не соответствует"
    )

    assert closed and closed[0][0] == 42


# +------------------- миграция -------------------+


def test_migration_034_allows_author_claim_on_pending_achievement() -> None:
    sql = (MIGRATIONS_DIR / "034_creator_top5_claim.sql").read_text(encoding="utf-8")

    assert "if v_ach_status = 'pending' then" in sql
    assert "v_creator is distinct from v_actor" in sql
    assert "create policy top5_applications_insert" in sql
    assert "a.status = 'pending'" in sql
    # Экспертные таблицы миграция не меняет.
    assert "alter table public.top5_expert_requests" not in sql
    assert "alter table public.top5_evaluations" not in sql
