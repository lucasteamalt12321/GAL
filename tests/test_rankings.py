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
_real_max_ranked_bt_rating = rankings_service.max_ranked_bt_rating


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


class _RecordingNot:
    def __init__(self, query: "_RecordingQuery") -> None:
        self.query = query

    def is_(self, *args):
        self.query.calls.append(("is", *args))
        return self.query


class _RecordingQuery:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple] = []
        self.count = len(rows)
        self.not_ = _RecordingNot(self)

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


def test_leaderboard_renders_players_with_search(monkeypatch) -> None:
    captured: list[dict] = []

    def _players_page(**kwargs):
        captured.append(kwargs)
        return _players(), 2

    monkeypatch.setattr(rankings_service, "player_leaderboard", _players_page)
    resp = client.get("/leaderboard?q=  ALI  ")
    assert resp.status_code == 200
    body = resp.text
    assert 'href="/leaderboard">Игроки</a>' in body
    assert "<h1>Игроки</h1>" in body
    assert 'name="q" value="ALI"' in body
    assert 'href="/users/alice"' in body
    assert "1500.0" in body
    assert "Топ достижений" not in body
    assert "Achievement 1" not in body
    assert captured == [{"page": 1, "query": "ALI"}]


def test_leaderboard_empty_state(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        rankings_service,
        "player_leaderboard",
        lambda **kwargs: captured.append(kwargs) or ([], 0),
    )

    resp = client.get("/leaderboard")

    assert resp.status_code == 200
    assert "Пока нет игроков с очками" in resp.text
    assert captured == [{"page": 1, "query": None}]


def test_leaderboard_search_empty_state(monkeypatch) -> None:
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: ([], 0))

    resp = client.get("/leaderboard?q=nobody")

    assert resp.status_code == 200
    assert "По запросу «nobody» игроки не найдены" in resp.text


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
    def __init__(
        self,
        error_for: dict[str, Exception],
        data_for: dict[str, list[dict]] | None = None,
    ) -> None:
        self.error_for = error_for
        self.data_for = data_for or {}
        self.calls: list[str] = []
        self.params: dict[str, dict] = {}

    def rpc(self, name: str, params: dict | None = None):
        self.calls.append(name)
        self.params[name] = params or {}
        error = self.error_for.get(name)
        if error is not None:
            return _RpcQuery(error)
        return SimpleNamespace(
            execute=lambda: SimpleNamespace(
                data=self.data_for.get(name, []), count=None
            )
        )


def _missing_function() -> APIError:
    return APIError(
        {
            "message": "Could not find the function public.player_leaderboard_search_page",
            "code": "PGRST202",
            "details": None,
            "hint": None,
        }
    )


def test_player_leaderboard_falls_back_when_rpc_missing(monkeypatch) -> None:
    fake = _RpcClient({"player_leaderboard_search_page": _missing_function()})
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    rows, total = _real_player_leaderboard()

    assert rows == []
    assert total == 0
    assert fake.calls == ["player_leaderboard_search_page", "player_leaderboard"]


def test_player_leaderboard_propagates_other_rpc_errors(monkeypatch) -> None:
    broken = APIError(
        {
            "message": "connection reset by peer",
            "code": "XX000",
            "details": None,
            "hint": None,
        }
    )
    fake = _RpcClient({"player_leaderboard_search_page": broken})
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    with pytest.raises(APIError):
        _real_player_leaderboard()
    assert fake.calls == ["player_leaderboard_search_page"]


def test_player_leaderboard_uses_search_rpc_and_total_count(monkeypatch) -> None:
    rows = [{**_players()[0], "total_count": 7}]
    fake = _RpcClient({}, {"player_leaderboard_search_page": rows})
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    page, total = _real_player_leaderboard(page=2, query="  Alice  ")

    assert page == rows
    assert total == 7
    assert fake.params["player_leaderboard_search_page"] == {
        "p_limit": 25,
        "p_offset": 25,
        "p_query": "Alice",
    }


def test_player_leaderboard_recovers_total_past_last_page(monkeypatch) -> None:
    class _OutOfRangeClient(_RpcClient):
        def rpc(self, name: str, params: dict | None = None):
            self.calls.append(name)
            self.params[name] = params or {}
            rows = [] if params and params["p_offset"] else [{"total_count": 80}]
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))

    fake = _OutOfRangeClient({})
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    rows, total = _real_player_leaderboard(page=4)

    assert rows == []
    assert total == 80
    assert fake.params["player_leaderboard_search_page"] == {
        "p_limit": 1,
        "p_offset": 0,
        "p_query": "",
    }


def test_player_leaderboard_fallback_searches_literally_and_sorts(monkeypatch) -> None:
    rows = [
        {"username": "zoe", "display_name": "Alice", "score": 100},
        {"username": "alice", "display_name": "ALICE", "score": 100},
        {"username": "percent", "display_name": "100%", "score": 200},
        {"username": "underscore", "display_name": "a_b", "score": 300},
        {"username": "other", "display_name": "Other", "score": 400},
    ]
    fake = _RpcClient(
        {"player_leaderboard_search_page": _missing_function()},
        {"player_leaderboard": rows},
    )
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    page, total = _real_player_leaderboard(query="ALICE", limit=1)

    assert [row["username"] for row in page] == ["alice"]
    assert total == 2
    page, total = _real_player_leaderboard(query="%", limit=1)
    assert [row["username"] for row in page] == ["percent"]
    assert total == 1
    page, total = _real_player_leaderboard(query="a_b")
    assert [row["username"] for row in page] == ["underscore"]
    assert total == 1


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


def test_leaderboard_pagination_preserves_search(monkeypatch) -> None:
    captured: list[dict] = []

    def _players_page(**kwargs):
        captured.append(kwargs)
        return _players(), 80

    monkeypatch.setattr(rankings_service, "player_leaderboard", _players_page)

    resp = client.get("/leaderboard?players_page=2&q=ali")

    assert resp.status_code == 200
    assert "Страница 2 из 4" in resp.text
    assert "players_page=3" in resp.text
    assert "q=ali" in resp.text
    assert captured == [{"page": 2, "query": "ali"}]


def test_leaderboard_out_of_range_redirects(monkeypatch) -> None:
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda **_: ([], 0))

    resp = client.get("/leaderboard?players_page=3&q=ali", follow_redirects=False)

    assert resp.status_code == 303
    assert "players_page=1" in resp.headers["location"]
    assert "q=ali" in resp.headers["location"]


def test_max_ranked_bt_rating_queries_only_eligible_achievements(
    monkeypatch,
) -> None:
    fake = _RecordingClient([{"bt_rating": 1280.0282}])
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    assert _real_max_ranked_bt_rating() == 1280.0282
    assert fake.query.calls == [
        ("select", ("bt_rating",), {}),
        ("eq", "status", "published"),
        ("eq", "ranking_status", "ranked"),
        ("is", "rank", None),
        ("is", "bt_rating", None),
        ("order", ("bt_rating",), {"desc": True}),
        ("limit", 1),
    ]


def test_max_ranked_bt_rating_returns_none_without_eligible_achievements(
    monkeypatch,
) -> None:
    fake = _RecordingClient([])
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: fake)

    assert _real_max_ranked_bt_rating() is None
