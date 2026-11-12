from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import completions as completions_service

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


def _sample_achievement() -> dict:
    return {
        "id": 1,
        "title": "Sample",
        "description": "d",
        "requirements": "r",
        "status": "published",
        "rank": 1,
        "average_position": 1.5,
        "evaluation_count": 3,
        "bt_strength": 0.7,
        "bt_rating": 1123.4,
        "bt_comparisons": 5,
        "bt_rating_se": 21.0,
        "bt_rating_ci_low": 1082.0,
        "bt_rating_ci_high": 1164.0,
        "creator_id": "00000000-0000-0000-0000-000000000000",
        "category": {"id": 1, "name": "Chess", "slug": "chess"},
        "creator": {
            "id": "00000000-0000-0000-0000-000000000000",
            "username": "alice",
            "display_name": "Alice",
            "avatar_url": None,
        },
    }


def test_list_page_renders(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(achievements_service, "list_achievements", lambda **_: ([], 0))
    resp = client.get("/achievements")
    assert resp.status_code == 200
    assert "Достижения" in resp.text


def test_list_page_shows_achievement(monkeypatch) -> None:
    monkeypatch.setattr(
        achievements_service,
        "list_categories",
        lambda: [{"id": 1, "name": "Chess", "slug": "chess"}],
    )
    monkeypatch.setattr(
        achievements_service,
        "list_achievements",
        lambda **_: ([_sample_achievement()], 1),
    )
    resp = client.get("/achievements")
    assert resp.status_code == 200
    assert "Sample" in resp.text
    assert "#1" in resp.text


def test_detail_404(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: None)
    resp = client.get("/achievements/999")
    assert resp.status_code == 404


def test_detail_renders(monkeypatch) -> None:
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _sample_achievement()
    )
    monkeypatch.setattr(
        completions_service, "list_achievement_completers", lambda *_: []
    )
    resp = client.get("/achievements/1")
    assert resp.status_code == 200
    assert "Sample" in resp.text
    assert "1123" in resp.text
    assert "± 21 · 95% ДИ 1082–1164" in resp.text
    assert "доверительный интервал" in resp.text


def test_detail_hides_interval_without_enough_comparisons(monkeypatch) -> None:
    achievement = {
        **_sample_achievement(),
        "bt_comparisons": 1,
        "bt_rating_se": None,
        "bt_rating_ci_low": None,
        "bt_rating_ci_high": None,
    }
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: achievement)
    monkeypatch.setattr(
        completions_service, "list_achievement_completers", lambda *_: []
    )
    resp = client.get("/achievements/1")
    assert resp.status_code == 200
    assert "95% ДИ" not in resp.text
    assert "нужно ≥ 2 сравнений для интервала" in resp.text


def test_detail_lists_completers(monkeypatch) -> None:
    monkeypatch.setattr(
        achievements_service, "get_achievement", lambda *_: _sample_achievement()
    )
    monkeypatch.setattr(
        completions_service,
        "list_achievement_completers",
        lambda *_: [
            {
                "user_id": "11111111-1111-1111-1111-111111111111",
                "username": "bob",
                "display_name": "Bob",
                "avatar_url": None,
                "approved_at": "2026-11-01T10:00:00+00:00",
            }
        ],
    )
    resp = client.get("/achievements/1")
    assert resp.status_code == 200
    assert "Прошедшие пользователи" in resp.text
    assert 'href="/users/bob"' in resp.text


def test_create_page_requires_auth() -> None:
    resp = client.get("/achievements/create", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_create_post_requires_auth() -> None:
    resp = client.post(
        "/achievements/create",
        data={"title": "Test"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_profile_404(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "get_profile", lambda *_: None)
    resp = client.get("/users/nobody")
    assert resp.status_code == 404


def test_profile_renders(monkeypatch) -> None:
    monkeypatch.setattr(
        achievements_service,
        "get_profile",
        lambda *_: {
            "id": "00000000-0000-0000-0000-000000000000",
            "username": "alice",
            "display_name": "Alice",
            "avatar_url": None,
            "role": "user",
        },
    )
    monkeypatch.setattr(
        achievements_service, "list_achievements_by_creator", lambda *_, **__: []
    )
    monkeypatch.setattr(
        completions_service, "list_user_completed_achievements", lambda *_: []
    )
    resp = client.get("/users/alice")
    assert resp.status_code == 200
    assert "@alice" in resp.text
    assert "Пройденные достижения" in resp.text


def test_profile_shows_completed_achievements(monkeypatch) -> None:
    monkeypatch.setattr(
        achievements_service,
        "get_profile",
        lambda *_: {
            "id": "00000000-0000-0000-0000-000000000000",
            "username": "alice",
            "display_name": "Alice",
            "avatar_url": None,
            "role": "user",
            "created_at": "2026-10-01T10:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        achievements_service, "list_achievements_by_creator", lambda *_, **__: []
    )
    monkeypatch.setattr(
        completions_service,
        "list_user_completed_achievements",
        lambda *_: [
            {
                "achievement_id": 7,
                "title": "Water",
                "rank": 2,
                "ranking_status": "ranked",
                "bt_rating": 1123.4,
                "bt_strength": 0.7,
                "evaluation_count": 4,
                "category_name": "Sport",
                "category_slug": "sport",
                "approved_at": "2026-11-01T10:00:00+00:00",
            }
        ],
    )
    resp = client.get("/users/alice")
    assert resp.status_code == 200
    assert "Water" in resp.text
    assert "#2" in resp.text
    assert "500.00" in resp.text


def test_create_page_blocked_without_evaluation(monkeypatch) -> None:
    _login(monkeypatch)
    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: False)
    monkeypatch.setattr(achievements_service, "list_categories", list)

    resp = client.get("/achievements/create")

    assert resp.status_code == 200
    assert "Сначала пройдите и оцените" in resp.text
    assert 'name="title"' not in resp.text
    assert 'href="/achievements"' in resp.text


def test_create_post_blocked_without_evaluation(monkeypatch) -> None:
    _login(monkeypatch)
    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: False)
    calls: list[str] = []
    monkeypatch.setattr(
        achievements_service,
        "create_achievement",
        lambda *_a, **_k: calls.append("called") or {"id": 99},
    )

    resp = client.post(
        "/achievements/create",
        data={"title": "My achievement"},
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "Сначала пройдите и оцените" in resp.text
    assert calls == []


class _Query:
    """Минимальная цепочка PostgREST-запроса для тестов выборки."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.eq_filters: dict[str, object] = {}
        self.in_filters: dict[str, set] = {}

    def select(self, *_args, **_kwargs) -> _Query:
        return self

    def eq(self, key: str, value: object) -> _Query:
        self.eq_filters[key] = value
        return self

    def in_(self, key: str, values: list) -> _Query:
        self.in_filters[key] = set(values)
        return self

    def execute(self) -> SimpleNamespace:
        rows = []
        for row in self.rows:
            if any(row.get(key) != value for key, value in self.eq_filters.items()):
                continue
            if any(
                row.get(key) not in values for key, values in self.in_filters.items()
            ):
                continue
            rows.append(row)
        return SimpleNamespace(data=rows, count=len(rows))


class _Client:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables

    def table(self, name: str) -> _Query:
        return _Query(self.tables[name])


def _gate_request(
    monkeypatch,
    *,
    evaluations: list[dict],
    achievements: list[dict],
) -> SimpleNamespace:
    fake = _Client({"evaluations": evaluations, "achievements": achievements})
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: fake)
    return SimpleNamespace(state=SimpleNamespace(user=USER))


def test_has_user_evaluation_ignores_deleted_achievement(monkeypatch) -> None:
    request = _gate_request(
        monkeypatch,
        evaluations=[{"achievement_id": 5, "user_id": USER.id}],
        achievements=[{"id": 5, "status": "deleted"}],
    )

    assert achievements_service.has_user_evaluation(request) is False


def test_has_user_evaluation_true_for_published_achievement(monkeypatch) -> None:
    request = _gate_request(
        monkeypatch,
        evaluations=[
            {"achievement_id": 5, "user_id": USER.id},
            {"achievement_id": 6, "user_id": USER.id},
        ],
        achievements=[
            {"id": 5, "status": "deleted"},
            {"id": 6, "status": "published"},
        ],
    )

    assert achievements_service.has_user_evaluation(request) is True


def test_create_achievement_service_blocks_without_evaluation(monkeypatch) -> None:
    request = SimpleNamespace(state=SimpleNamespace(user=USER))
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: object())
    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: False)

    with pytest.raises(PermissionError, match="оцените"):
        achievements_service.create_achievement(
            request,
            title="New one",
            description="",
            requirements="",
            category_id=None,
        )
