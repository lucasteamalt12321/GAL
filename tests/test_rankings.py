from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.main import app
from app.services import auth as auth_service
from app.services import rankings as rankings_service
from app.services.rankings import leaderboard_achievements as fetch_ranked_page

client = TestClient(app)

# conftest подменяет сервис ради офлайн-лендинга; тест сервиса ниже вызывает
# исходную функцию, сохранённую до применения autouse-фикстуры.
_real_leaderboard_achievements = rankings_service.leaderboard_achievements
_real_player_leaderboard = rankings_service.player_leaderboard


class _FakeQuery:
    def __init__(self, fake_client: "_FakeClient") -> None:
        self.fake_client = fake_client
        self.select_arg = ""

    def select(self, select: str, **_kwargs):
        self.select_arg = select
        return self

    def eq(self, *_args):
        return self

    def gte(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        self.fake_client.selects.append(self.select_arg)
        if self.select_arg == "id":
            return SimpleNamespace(data=[], count=1)
        is_view = self.select_arg == rankings_service.VIEW_SELECT
        if is_view:
            if self.fake_client.view_error is not None:
                raise APIError(
                    {
                        "message": self.fake_client.view_error,
                        "code": self.fake_client.view_error_code,
                        "details": None,
                        "hint": None,
                    }
                )
            return SimpleNamespace(data=[{"id": 7, "title": "With CI"}], count=None)
        if any(
            field in self.select_arg for field in rankings_service.UNCERTAINTY_FIELDS
        ):
            if self.fake_client.table_error is not None:
                raise APIError(
                    {
                        "message": self.fake_client.table_error,
                        "code": self.fake_client.table_error_code,
                        "details": None,
                        "hint": None,
                    }
                )
            return SimpleNamespace(data=[{"id": 7, "title": "With CI"}], count=None)
        return SimpleNamespace(data=[{"id": 7, "title": "Legacy"}], count=None)


class _FakeClient:
    def __init__(
        self,
        view_error: str | None = None,
        table_error: str | None = None,
        view_error_code: str = "PGRST205",
        table_error_code: str = "PGRST204",
    ) -> None:
        self.view_error = view_error
        self.table_error = table_error
        self.view_error_code = view_error_code
        self.table_error_code = table_error_code
        self.selects: list[str] = []

    def table(self, _name: str) -> _FakeQuery:
        return _FakeQuery(self)


def _achievement(
    aid: int,
    rank: int | None,
    avg: float = 1.5,
    count: int = 3,
    *,
    rating: float | None = 1200.0,
    se: float | None = 18.0,
    ci_low: float | None = 1165.0,
    ci_high: float | None = 1235.0,
    comparisons: int = 6,
) -> dict:
    return {
        "id": aid,
        "title": f"Achievement {aid}",
        "rank": rank,
        "ranking_status": "ranked" if rank is not None else "unknown",
        "average_position": avg,
        "evaluation_count": count,
        "bt_rating": rating,
        "bt_comparisons": comparisons,
        "bt_rating_se": se,
        "bt_rating_ci_low": ci_low,
        "bt_rating_ci_high": ci_high,
        "category": {"id": 1, "name": "Chess", "slug": "chess"},
    }


def _preliminary(aid: int, strength: float) -> dict:
    row = _achievement(aid, None, avg=1.0, count=1)
    row["bt_strength"] = strength
    row["bt_rating"] = 1000 + 400 * strength
    row["bt_comparisons"] = 1
    return row


class _RecordingQuery:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple] = []
        self.count = len(rows)

    def select(self, *args, **kwargs):
        self.calls.append(("select", args, kwargs))
        return self

    def eq(self, *args):
        self.calls.append(("eq", *args))
        return self

    def gte(self, *args):
        self.calls.append(("gte", *args))
        return self

    def limit(self, *args):
        self.calls.append(("limit", *args))
        return self

    def order(self, *args, **kwargs):
        self.calls.append(("order", args, kwargs))
        return self

    def range(self, *args):
        self.calls.append(("range", *args))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows, count=self.count)


class _RecordingClient:
    def __init__(self, rows: list[dict]) -> None:
        self.query = _RecordingQuery(rows)

    def table(self, _name: str) -> _RecordingQuery:
        return self.query


def _players() -> list[dict]:
    return [
        {
            "username": "alice",
            "display_name": "Alice",
            "score": 1500.0,
            "achievement_count": 2,
        },
        {
            "username": "bob",
            "display_name": "Bob",
            "score": 500.0,
            "achievement_count": 1,
        },
    ]


def test_leaderboard_renders_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service,
        "leaderboard_achievements",
        lambda **_: ([_achievement(1, 1, 1.0, 5), _achievement(2, 2, 2.33, 3)], 2),
    )
    monkeypatch.setattr(
        rankings_service, "player_leaderboard", lambda **_: (_players(), 2)
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    body = resp.text
    assert "Рейтинг" in body
    assert "Топ достижений" in body
    assert "Achievement 1" in body
    assert "Alice" in body
    assert "1500.0" in body
    assert "1200" in body
    assert "± 18 · 95% ДИ 1165–1235" in body


def test_leaderboard_omits_interval_without_enough_data(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service,
        "leaderboard_achievements",
        lambda **_: (
            [_achievement(1, 1, se=None, ci_low=None, ci_high=None, comparisons=1)],
            1,
        ),
    )
    monkeypatch.setattr(
        rankings_service, "player_leaderboard", lambda **_: (_players(), 2)
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    assert "± " not in resp.text
    assert "1165" not in resp.text


def test_leaderboard_service_falls_back_before_migration(monkeypatch) -> None:
    fake_client = _FakeClient(
        view_error=(
            "Could not find the table 'public.public_achievements' in the schema cache"
        ),
        table_error="Could not find the 'bt_rating_se' column in the schema cache",
    )
    monkeypatch.setattr(
        rankings_service.auth_service, "new_anon_client", lambda: fake_client
    )

    rows, total = fetch_ranked_page()

    assert total == 1
    assert rows == [{"id": 7, "title": "Legacy"}]
    assert fake_client.selects == [
        "id",
        rankings_service.VIEW_SELECT,
        "id",
        rankings_service.RANKED_SELECT,
        rankings_service.LEGACY_RANKED_SELECT,
    ]


def test_leaderboard_service_propagates_view_column_error(monkeypatch) -> None:
    fake_client = _FakeClient(
        view_error=(
            "Could not find the 'sort_strength' column of "
            "'public_achievements' in the schema cache"
        ),
        view_error_code="PGRST204",
    )
    monkeypatch.setattr(
        rankings_service.auth_service, "new_anon_client", lambda: fake_client
    )

    with pytest.raises(APIError):
        fetch_ranked_page()
    assert fake_client.selects == ["id", rankings_service.VIEW_SELECT]


class _RpcQuery:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self):
        raise self.error


class _RpcClient:
    def __init__(self, error_for: dict[str, Exception]) -> None:
        self.error_for = error_for
        self.calls: list[str] = []

    def rpc(self, name: str, _params: dict | None = None):
        self.calls.append(name)
        error = self.error_for.get(name)
        if error is not None:
            return _RpcQuery(error)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[], count=None))


def _missing_function() -> APIError:
    return APIError(
        {
            "message": "Could not find the function public.player_leaderboard_page",
            "code": "PGRST202",
            "details": None,
            "hint": None,
        }
    )


def test_player_leaderboard_falls_back_when_rpc_missing(monkeypatch) -> None:
    fake = _RpcClient({"player_leaderboard_page": _missing_function()})
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    rows, total = _real_player_leaderboard()

    assert rows == []
    assert total == 0
    assert fake.calls == ["player_leaderboard_page", "player_leaderboard"]


def test_player_leaderboard_propagates_other_rpc_errors(monkeypatch) -> None:
    broken = APIError(
        {
            "message": "connection reset by peer",
            "code": "XX000",
            "details": None,
            "hint": None,
        }
    )
    fake = _RpcClient({"player_leaderboard_page": broken})
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    with pytest.raises(APIError):
        _real_player_leaderboard()
    assert fake.calls == ["player_leaderboard_page"]


def test_leaderboard_service_uses_view_after_migration(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        rankings_service.auth_service, "new_anon_client", lambda: fake_client
    )

    rows, total = fetch_ranked_page()

    assert total == 1
    assert rows == [{"id": 7, "title": "With CI"}]
    assert fake_client.selects == ["id", rankings_service.VIEW_SELECT]


def test_leaderboard_service_propagates_other_storage_errors(monkeypatch) -> None:
    fake_client = _FakeClient(
        view_error="permission denied for table public_achievements",
        view_error_code="42501",
    )
    monkeypatch.setattr(
        rankings_service.auth_service, "new_anon_client", lambda: fake_client
    )

    with pytest.raises(APIError):
        fetch_ranked_page()
    assert fake_client.selects == ["id", rankings_service.VIEW_SELECT]


def test_leaderboard_survives_missing_uncertainty_columns(monkeypatch) -> None:
    row = _achievement(1, 1)
    for key in ("bt_rating_se", "bt_rating_ci_low", "bt_rating_ci_high"):
        row.pop(key)
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([row], 1)
    )
    monkeypatch.setattr(
        rankings_service, "player_leaderboard", lambda **_: (_players(), 2)
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    assert "Achievement 1" in resp.text


def test_leaderboard_empty_state(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([], 0)
    )
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: ([], 0))
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    assert "Пока нет оценённых достижений" in resp.text
    assert "Пока нет очков" in resp.text


def test_leaderboard_interleaves_preliminary_without_renumbering(monkeypatch) -> None:
    rows = [
        _preliminary(31, 1.25),
        _achievement(46, 1, 1.5, 2),
        _preliminary(36, -0.38),
        _achievement(33, 2, 2.0, 2),
        _achievement(35, 3, 3.0, 3),
    ]
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: (rows, len(rows))
    )
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: ([], 0))
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    body = resp.text
    # Шаблон сохраняет порядок сервиса: предварительные строки стоят выше
    # или ниже оценённых, но не сдвигают их номера.
    assert body.index("Achievement 31") < body.index("Achievement 46")
    assert body.index("Achievement 36") < body.index("Achievement 33")
    assert "#1" in body
    assert "#2" in body
    assert "#3" in body
    assert "#None" not in body
    assert "lb-rank-unknown" in body


def test_leaderboard_marks_temporary_rank(monkeypatch) -> None:
    row = _achievement(5, 3, avg=1.0, count=1)
    row["is_temporary"] = True
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([row], 1)
    )
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: ([], 0))
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    assert '#3<span title="Временное место в топ-5">*</span>' in resp.text
    assert "«*» — временное место в топ-5" in resp.text


def test_leaderboard_service_keeps_single_evaluation_rows(monkeypatch) -> None:
    rows = [_preliminary(31, 1.25)]
    fake = _RecordingClient(rows)
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)
    data, total = _real_leaderboard_achievements()
    assert data == rows
    assert total == 1
    filters = [call for call in fake.query.calls if call[0] in ("eq", "gte")]
    assert ("eq", "in_leaderboard", True) in filters
    assert all(
        not (call[0] == "eq" and call[1] == "ranking_status") for call in filters
    )
    first_order = next(call for call in fake.query.calls if call[0] == "order")
    assert first_order == (
        "order",
        ("sort_strength",),
        {"desc": True, "nullsfirst": False},
    )


def test_leaderboard_keeps_service_order(monkeypatch) -> None:
    rows = [
        {
            "username": "low",
            "display_name": "Low",
            "score": 500.0,
            "achievement_count": 1,
        },
        {
            "username": "high",
            "display_name": "High",
            "score": 2500.0,
            "achievement_count": 3,
        },
    ]
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: (rows, 2))
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([], 0)
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    body = resp.text
    assert body.index("Low") < body.index("High")


def test_leaderboard_pagination_links(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service,
        "leaderboard_achievements",
        lambda **_: ([_achievement(1, 1)], 80),
    )
    monkeypatch.setattr(
        rankings_service, "player_leaderboard", lambda **_: (_players(), 80)
    )
    resp = client.get("/leaderboard?page=2&players_page=2")
    assert resp.status_code == 200
    assert "Страница 2 из 4" in resp.text
    assert "page=3" in resp.text
    assert "players_page=3" in resp.text


def test_leaderboard_out_of_range_redirects(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service, "leaderboard_achievements", lambda **_: ([], 0)
    )
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: ([], 0))

    resp = client.get("/leaderboard?page=2", follow_redirects=False)
    assert resp.status_code == 303
    assert "page=1" in resp.headers["location"]

    resp = client.get("/leaderboard?players_page=3", follow_redirects=False)
    assert resp.status_code == 303
    assert "players_page=1" in resp.headers["location"]
