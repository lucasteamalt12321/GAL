from __future__ import annotations

import json

import httpx

from app.supabase_http import (
    SupabaseBatchingTransport,
    new_supabase_httpx_client,
)


def test_table_pages_are_batched_and_count_is_preserved() -> None:
    calls: list[tuple[int, int]] = []
    rows = [{"id": value} for value in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        calls.append((offset, limit))
        page = rows[offset : offset + limit]
        end = offset + len(page) - 1
        return httpx.Response(
            200,
            json=page,
            headers={"content-range": f"{offset}-{end}/{len(rows)}"},
        )

    inner = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SupabaseBatchingTransport(2, client=inner)
    with httpx.Client(
        transport=transport, base_url="https://test.supabase.co"
    ) as client:
        response = client.get(
            "/rest/v1/items",
            params={"select": "id", "limit": 5, "offset": 0},
        )

    assert response.status_code == 200
    assert response.json() == rows
    assert response.headers["content-range"] == "0-4/5"
    assert calls == [(0, 1), (1, 1), (2, 1), (3, 1), (4, 1)]


def test_repeated_order_params_are_preserved() -> None:
    orders: list[list[str]] = []
    prefers: list[str] = []
    rows = [{"id": value} for value in range(3)]

    def handler(request: httpx.Request) -> httpx.Response:
        orders.append(request.url.params.get_list("order"))
        prefers.append(request.headers.get("prefer", ""))
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json=rows[offset : offset + 1],
            headers={"content-range": f"{offset}-{offset}/3"},
        )

    inner = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SupabaseBatchingTransport(2, client=inner)
    with httpx.Client(
        transport=transport, base_url="https://test.supabase.co"
    ) as client:
        response = client.get(
            "/rest/v1/items",
            headers={"prefer": "count=exact"},
            params=[
                ("select", "id"),
                ("limit", 3),
                ("offset", 0),
                ("order", "rank.asc"),
                ("order", "id.asc"),
            ],
        )

    assert response.json() == rows
    assert orders == [["rank.asc", "id.asc"]] * 3
    assert prefers == ["", "", ""]


def test_unknown_table_count_stops_at_end_of_rows() -> None:
    calls: list[int] = []
    rows = [{"id": value} for value in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        calls.append(offset)
        page = rows[offset : offset + limit]
        end = offset + len(page) - 1
        return httpx.Response(
            200,
            json=page,
            headers={"content-range": f"{offset}-{end}/*"},
        )

    inner = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SupabaseBatchingTransport(2, client=inner)
    with httpx.Client(
        transport=transport, base_url="https://test.supabase.co"
    ) as client:
        response = client.get(
            "/rest/v1/items",
            params={"select": "id", "limit": 10, "offset": 0},
        )

    assert response.json() == rows
    assert response.headers["content-range"] == "0-4/*"
    assert sorted(calls) == [0, 1, 2, 3, 4, 5, 6, 7, 8]


def test_rpc_pages_are_batched_and_query_is_preserved() -> None:
    calls: list[tuple[int, int, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        offset = payload["p_offset"]
        limit = payload["p_limit"]
        calls.append((offset, limit, payload["p_query"]))
        page = [
            {"username": f"user-{value}", "total_count": 25}
            for value in range(offset, min(offset + limit, 25))
        ]
        return httpx.Response(200, json=page)

    inner = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SupabaseBatchingTransport(2, client=inner)
    with httpx.Client(
        transport=transport, base_url="https://test.supabase.co"
    ) as client:
        response = client.post(
            "/rest/v1/rpc/player_leaderboard_page",
            json={"p_limit": 25, "p_offset": 0, "p_query": "user"},
        )

    assert response.status_code == 200
    assert len(response.json()) == 25
    assert calls == [
        (0, 2, "user"),
        (2, 2, "user"),
        (4, 2, "user"),
        (6, 2, "user"),
        (8, 2, "user"),
        (10, 2, "user"),
        (12, 2, "user"),
        (14, 2, "user"),
        (16, 2, "user"),
        (18, 2, "user"),
        (20, 2, "user"),
        (22, 2, "user"),
        (24, 1, "user"),
    ]


def test_small_object_response_is_not_batched() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": "user-1"})

    inner = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SupabaseBatchingTransport(2, client=inner)
    with httpx.Client(
        transport=transport, base_url="https://test.supabase.co"
    ) as client:
        response = client.get(
            "/rest/v1/profiles",
            params={"select": "id", "limit": 1, "offset": 0},
        )

    assert response.json() == {"id": "user-1"}
    assert calls == ["/rest/v1/profiles"]


def test_timeout_is_retried_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("temporary", request=request)
        return httpx.Response(200, json={"id": "user-1"})

    inner = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SupabaseBatchingTransport(2, client=inner)
    with httpx.Client(
        transport=transport, base_url="https://test.supabase.co"
    ) as client:
        response = client.get(
            "/rest/v1/profiles",
            params={"select": "id", "limit": 1, "offset": 0},
        )

    assert response.json() == {"id": "user-1"}
    assert calls == 2


def test_http_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": "database unavailable"})

    inner = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SupabaseBatchingTransport(2, client=inner)
    with httpx.Client(
        transport=transport, base_url="https://test.supabase.co"
    ) as client:
        response = client.get(
            "/rest/v1/profiles",
            params={"select": "id", "limit": 1, "offset": 0},
        )

    assert response.status_code == 500
    assert calls == 1


def test_complete_json_is_accepted_when_final_chunk_times_out() -> None:
    class MissingTerminator(httpx.SyncByteStream):
        def __iter__(self):
            yield b'[{"id": 1}]'
            raise httpx.ReadTimeout("missing final chunk")

    inner = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"transfer-encoding": "chunked"},
                stream=MissingTerminator(),
            )
        )
    )
    transport = SupabaseBatchingTransport(2, client=inner)
    with httpx.Client(
        transport=transport, base_url="https://test.supabase.co"
    ) as client:
        response = client.get(
            "/rest/v1/profiles",
            params={"select": "id", "limit": 1, "offset": 0},
        )

    assert response.json() == [{"id": 1}]
    assert response.headers["content-length"] == "11"
    assert "transfer-encoding" not in response.headers


def test_client_factory_enables_batching_only_in_appwrite(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_BATCH_POSTGREST", raising=False)
    client = new_supabase_httpx_client(2)
    try:
        assert isinstance(client._transport, httpx.HTTPTransport)
        assert client._transport._pool._http2 is False
    finally:
        client.close()

    monkeypatch.setenv("SUPABASE_BATCH_POSTGREST", "1")
    client = new_supabase_httpx_client(2)
    try:
        assert isinstance(client._transport, SupabaseBatchingTransport)
    finally:
        client.close()
