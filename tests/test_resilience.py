"""Сбои Supabase/PostgREST не должны давать голый 500 — только 503."""

import httpx
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.main import app
from app.services import achievements as achievements_service
from app.services import rankings as rankings_service

client = TestClient(app)


def _raise(exc: Exception):
    def _boom(*_args, **_kwargs):
        raise exc

    return _boom


def test_leaderboard_read_timeout_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service,
        "leaderboard_achievements",
        _raise(httpx.ReadTimeout("The read operation timed out")),
    )
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda *_: [])
    resp = client.get("/leaderboard")
    assert resp.status_code == 503
    assert "временно недоступен" in resp.text


def test_leaderboard_remote_protocol_error_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(rankings_service, "leaderboard_achievements", lambda *_: [])
    monkeypatch.setattr(
        rankings_service,
        "player_leaderboard",
        _raise(httpx.RemoteProtocolError("Server disconnected")),
    )
    resp = client.get("/leaderboard")
    assert resp.status_code == 503


def test_leaderboard_postgrest_error_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_service,
        "leaderboard_achievements",
        _raise(APIError({"message": "upstream boom", "code": "57014"})),
    )
    monkeypatch.setattr(rankings_service, "player_leaderboard", lambda *_: [])
    resp = client.get("/leaderboard")
    assert resp.status_code == 503


def test_achievements_list_upstream_error_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(
        achievements_service,
        "list_achievements",
        _raise(httpx.ConnectError("Connection refused")),
    )
    resp = client.get("/achievements")
    assert resp.status_code == 503
