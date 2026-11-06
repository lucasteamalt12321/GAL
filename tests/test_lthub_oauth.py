from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import app.routers.lthub_auth as lthub_router
from app.config import Settings
from app.main import app
from app.services import lthub_accounts

client = TestClient(app)


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _enable(monkeypatch) -> None:
    monkeypatch.setattr(
        lthub_router,
        "get_settings",
        lambda: Settings(
            app_env="development",
            app_url="https://gal.test",
            lthub_oauth_enabled=True,
            lthub_client_id="gal",
            lthub_authorize_url="https://lthub.test/oauth/authorize",
            lthub_token_url="https://lthub.test/oauth/token",
            lthub_userinfo_url="https://lthub.test/oauth/userinfo",
            lthub_redirect_uri="https://gal.test/auth/lthub/callback",
        ),
    )


def test_lthub_start_redirects_with_pkce(monkeypatch) -> None:
    _enable(monkeypatch)
    local = TestClient(app)
    resp = local.get("/auth/lthub", follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("https://lthub.test/oauth/authorize?")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == ["gal"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43
    assert query["redirect_uri"] == ["https://gal.test/auth/lthub/callback"]
    assert "gal_lthub_state" in local.cookies
    assert "gal_lthub_verifier" in local.cookies


def test_lthub_callback_rejects_wrong_state(monkeypatch) -> None:
    _enable(monkeypatch)
    local = TestClient(app)
    local.cookies.set("gal_lthub_state", "expected", path="/auth/lthub")
    local.cookies.set("gal_lthub_verifier", "v" * 64, path="/auth/lthub")
    resp = local.get(
        "/auth/lthub/callback?code=abc&state=wrong",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/auth/login?error=")


def test_lthub_callback_creates_session(monkeypatch) -> None:
    _enable(monkeypatch)

    def _post(*_args, **_kwargs):
        return _Response({"access_token": "lthub-token"})

    def _get(*_args, **_kwargs):
        return _Response({"sub": "42", "login": "alice", "display_name": "Alice"})

    monkeypatch.setattr(lthub_router.httpx, "post", _post)
    monkeypatch.setattr(lthub_router.httpx, "get", _get)
    captured: list[dict] = []

    def _login(_request, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(access_token="access", refresh_token="refresh")

    monkeypatch.setattr(lthub_accounts, "login_with_lthub", _login)
    local = TestClient(app)
    local.cookies.set("gal_lthub_state", "expected", path="/auth/lthub")
    local.cookies.set("gal_lthub_verifier", "v" * 64, path="/auth/lthub")
    resp = local.get(
        "/auth/lthub/callback?code=abc&state=expected",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert captured == [{"subject": "42", "login": "alice", "display_name": "Alice"}]
    assert "gal_session" in resp.cookies


def test_lthub_callback_surfaces_oauth_error(monkeypatch) -> None:
    _enable(monkeypatch)
    local = TestClient(app)
    resp = local.get(
        "/auth/lthub/callback?error=access_denied",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "LTHub" in resp.headers["location"]


def test_username_and_email_are_deterministic() -> None:
    assert lthub_accounts._email_for("42") == "lthub-oauth-42@example.com"
    assert lthub_accounts._username_for("42") == "lthub_42"
    long_subject = "a" * 64
    assert len(lthub_accounts._username_for(long_subject)) <= 32
    assert lthub_accounts._username_for(long_subject).startswith("lthub_")


def test_username_prefers_lthub_login() -> None:
    assert lthub_accounts._username_for("8", "shadowraven") == "shadowraven"
    assert lthub_accounts._sanitize_username("shadow raven!") == "shadowraven"
    assert lthub_accounts._sanitize_username("ab") is None
    candidates = lthub_accounts._username_candidates("8", "shadowraven")
    assert candidates[0] == "shadowraven"
    assert candidates[-1] == "lthub_8"
