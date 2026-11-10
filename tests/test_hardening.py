"""Регрессии по итогам аудита: XSS в ссылках, заголовки, ошибки, сессия.

Каждый тест закрепляет конкретный инвариант, который раньше нарушался:
- ссылка-доказательство с `javascript:` попадала в href;
- HTML-страницы отвечали JSON 422 на некорректные query-параметры;
- сетевой сбой Supabase удалял cookies и разлогинивал пользователя;
- LTHub-аккаунт мог быть найден по изменяемому username.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from starlette.requests import Request
from supabase_auth.errors import (
    AuthApiError,
    AuthInvalidJwtError,
    AuthSessionMissingError,
)

from app.config import Settings
from app.main import app
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import lthub_accounts

client = TestClient(app)


# --- XSS в ссылках-доказательствах -----------------------------------------


def test_link_proof_allows_only_http_schemes() -> None:
    for bad in ("javascript:alert(1)", "data:text/html,<script>x</script>", "  "):
        view = completions_service.proof_view(
            object(), {"proof_type": "link", "storage_path": bad}
        )
        assert view["url"] is None

    view = completions_service.proof_view(
        object(), {"proof_type": "link", "storage_path": "https://example.com/proof"}
    )
    assert view["url"] == "https://example.com/proof"


def test_safe_external_url_trims_and_validates() -> None:
    assert completions_service.safe_external_url("  http://example.com  ") == (
        "http://example.com"
    )
    assert completions_service.safe_external_url("ftp://example.com") is None
    assert completions_service.safe_external_url(None) is None


# --- Security-заголовки -----------------------------------------------------


def test_security_headers_present() -> None:
    resp = client.get("/")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]


# --- Ошибки валидации как HTML ---------------------------------------------


def test_invalid_query_returns_html_error() -> None:
    resp = client.get("/achievements?page=abc")
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("text/html")
    assert "Некорректные данные запроса" in resp.text


def test_unknown_page_returns_html_404() -> None:
    resp = client.get("/this-page-does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")


# --- Сессия при сбое Supabase ----------------------------------------------


def _request_with_session() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", b"gal_session=a.b.c; gal_refresh=refresh-token")],
    }
    request = Request(scope)
    request.state.pending_cookies = []
    return request


def _patch_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: Settings(
            supabase_url="https://test.supabase.co", supabase_anon_key="anon"
        ),
    )
    monkeypatch.setattr(auth_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(auth_service, "_NETWORK_RETRIES", 1)


class _FailingAuth:
    def __init__(self, error: Exception, refresh_error: Exception) -> None:
        self.error = error
        self.refresh_error = refresh_error

    def get_user(self, jwt: str):
        raise self.error

    def refresh_session(self, refresh: str):
        raise self.refresh_error


def _patch_client(monkeypatch, auth_stub: _FailingAuth) -> None:
    monkeypatch.setattr(
        auth_service,
        "new_anon_client",
        lambda: type("_Client", (), {"auth": auth_stub})(),
    )


def test_network_failure_keeps_session_cookies(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    _patch_client(
        monkeypatch,
        _FailingAuth(
            httpx.ConnectError("upstream down"), httpx.ConnectError("upstream down")
        ),
    )
    request = _request_with_session()
    assert auth_service.resolve_user(request) is None
    # cookies не помечены на удаление
    assert request.state.pending_cookies == []


def test_invalid_session_drops_cookies(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    _patch_client(
        monkeypatch,
        _FailingAuth(AuthInvalidJwtError("expired"), AuthSessionMissingError()),
    )
    request = _request_with_session()
    assert auth_service.resolve_user(request) is None
    dropped = {
        name
        for name, _value, kwargs in request.state.pending_cookies
        if kwargs.get("max_age") == 0
    }
    assert {"gal_session", "gal_refresh"} <= dropped


# --- LTHub: поиск только по детерминированному email ------------------------


class _AdminUsers:
    def __init__(self, users: list[object]) -> None:
        self.users = users

    def list_users(self, page: int | None = None, per_page: int | None = None):
        return self.users


class _FakeService:
    def __init__(self, users: list[object]) -> None:
        self.auth = type("_Auth", (), {"admin": _AdminUsers(users)})()


def test_lthub_finds_user_by_email_case_insensitively() -> None:
    user = type("_User", (), {"id": "user-1", "email": "lthub-oauth-7@example.com"})()
    service = _FakeService([user])
    found = lthub_accounts._find_user_id_by_email(service, "LTHub-OAuth-7@example.com")
    assert found == "user-1"


def test_lthub_does_not_match_foreign_email() -> None:
    user = type("_User", (), {"id": "user-1", "email": "someone@example.com"})()
    service = _FakeService([user])
    assert (
        lthub_accounts._find_user_id_by_email(service, "lthub-oauth-7@example.com")
        is None
    )


def test_lthub_create_failure_never_reuses_profile_by_username(monkeypatch) -> None:
    """При сбое create_user ищем только по email, а не по username."""

    class _Admin:
        def create_user(self, payload):
            raise AuthApiError("already registered", 422, "email_exists")

        def list_users(self, page=None, per_page=None):
            return []

    class _Service:
        auth = type("_Auth", (), {"admin": _Admin()})()

    service = _Service()
    monkeypatch.setattr(lthub_accounts, "_sync_profile", lambda *a, **k: None)
    monkeypatch.setattr(lthub_accounts, "_pick_username", lambda *a, **k: "shadowraven")
    try:
        lthub_accounts._create_or_reuse_user(
            service, subject="7", login="shadowraven", display_name="Shadow"
        )
    except lthub_accounts.LthubAccountError:
        pass
    else:  # pragma: no cover - защита от регрессии
        raise AssertionError("must not reuse a profile found by username")
