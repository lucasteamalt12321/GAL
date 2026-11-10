"""OAuth provider tests: authorization code + PKCE flow against a fake Supabase."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.services.oauth_provider as provider
from app.main import app
from app.routers.auth import _safe_next
from app.services import auth as auth_service

client = TestClient(app)

PROFILE_ID = "6f1d2c3b-4a5e-4f60-8a7b-9c0d1e2f3a4b"
VERIFIER = "v" * 64
REDIRECT_URI = "https://lthub.vercel.app/api/auth/gal/callback"


class FakeQuery:
    def __init__(self, store: dict[str, list[dict]], table: str) -> None:
        self.store = store
        self.table = table
        self.op = "select"
        self.payload: dict | None = None
        self.filters: list[tuple[str, str, object]] = []
        self.limit_value: int | None = None

    def select(self, *_args, **_kwargs):
        self.op = "select"
        return self

    def insert(self, data: dict):
        self.op = "insert"
        self.payload = dict(data)
        return self

    def update(self, data: dict):
        self.op = "update"
        self.payload = dict(data)
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, column: str, value):
        self.filters.append((column, "eq", value))
        return self

    def is_(self, column: str, value):
        self.filters.append((column, "is", value))
        return self

    def lt(self, column: str, value):
        self.filters.append((column, "lt", value))
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def _match(self, row: dict) -> bool:
        for column, kind, value in self.filters:
            if kind == "eq" and row.get(column) != value:
                return False
            if kind == "is" and value == "null" and row.get(column) is not None:
                return False
            if kind == "lt" and not (
                row.get(column) is not None and row[column] < value
            ):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "insert":
            rows.append(self.payload or {})
            return SimpleNamespace(data=[self.payload])
        if self.op == "update":
            updated = []
            for row in rows:
                if self._match(row):
                    row.update(self.payload or {})
                    updated.append(dict(row))
            return SimpleNamespace(data=updated)
        if self.op == "delete":
            removed = [row for row in rows if self._match(row)]
            self.store[self.table] = [row for row in rows if not self._match(row)]
            return SimpleNamespace(data=removed)
        selected = [dict(row) for row in rows if self._match(row)]
        if self.limit_value is not None:
            selected = selected[: self.limit_value]
        return SimpleNamespace(data=selected)


class FakeClient:
    def __init__(self, store: dict[str, list[dict]]) -> None:
        self.store = store

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self.store, name)


def _login_as(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_service,
        "resolve_user",
        lambda request: (
            auth_service.CurrentUser(
                id=PROFILE_ID,
                email="alice@example.com",
                username="alice",
                display_name="Alice",
                avatar_url=None,
                role="user",
            )
            if request.cookies.get("gal_session")
            else None
        ),
    )


def _fake_provider(monkeypatch) -> dict[str, list[dict]]:
    store: dict[str, list[dict]] = {
        "profiles": [
            {
                "id": PROFILE_ID,
                "username": "alice",
                "display_name": "Alice",
                "avatar_url": None,
            }
        ]
    }
    monkeypatch.setattr(provider, "get_service_client", lambda: FakeClient(store))
    return store


def _authorize_query(challenge: str) -> str:
    return (
        "/oauth/authorize?response_type=code&client_id=lthub"
        f"&redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}"
        f"&state=state-1&code_challenge={challenge}&code_challenge_method=S256"
    )


def test_authorize_redirects_anonymous_to_login(monkeypatch) -> None:
    local = TestClient(app)
    response = local.get(
        _authorize_query(provider.pkce_challenge(VERIFIER)), follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/login?next=")


def test_authorize_page_renders_consent(monkeypatch) -> None:
    _login_as(monkeypatch)
    _fake_provider(monkeypatch)
    local = TestClient(app)
    local.cookies.set("gal_session", "session")
    response = local.get(_authorize_query(provider.pkce_challenge(VERIFIER)))
    assert response.status_code == 200
    assert "LTHub" in response.text
    assert "alice" in response.text
    assert 'name="decision"' in response.text


def test_full_code_flow_and_token_reuse(monkeypatch) -> None:
    _login_as(monkeypatch)
    store = _fake_provider(monkeypatch)
    local = TestClient(app)
    local.cookies.set("gal_session", "session")
    challenge = provider.pkce_challenge(VERIFIER)

    consent = local.post(
        "/oauth/authorize",
        data={
            "client_id": "lthub",
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-1",
            "scope": "profile",
            "decision": "allow",
        },
        follow_redirects=False,
    )
    assert consent.status_code == 303
    location = consent.headers["location"]
    assert location.startswith(REDIRECT_URI + "?")
    assert "state=state-1" in location
    code = location.split("code=", 1)[1].split("&", 1)[0]
    assert len(store["oauth_codes"]) == 1

    token_response = local.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "lthub",
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": VERIFIER,
        },
    )
    assert token_response.status_code == 200
    payload = token_response.json()
    assert payload["token_type"] == "Bearer"
    assert payload["user"]["sub"] == PROFILE_ID
    access_token = payload["access_token"]

    info = local.get(
        "/oauth/userinfo", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert info.status_code == 200
    assert info.json()["preferred_username"] == "alice"
    assert info.json()["display_name"] == "Alice"

    reused = local.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "lthub",
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": VERIFIER,
        },
    )
    assert reused.status_code == 400
    assert reused.json()["error"] == "invalid_grant"


def test_wrong_verifier_rejected(monkeypatch) -> None:
    _login_as(monkeypatch)
    _fake_provider(monkeypatch)
    local = TestClient(app)
    local.cookies.set("gal_session", "session")
    challenge = provider.pkce_challenge(VERIFIER)
    location = local.post(
        "/oauth/authorize",
        data={
            "client_id": "lthub",
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-1",
            "decision": "allow",
        },
        follow_redirects=False,
    ).headers["location"]
    code = location.split("code=", 1)[1].split("&", 1)[0]
    response = local.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "lthub",
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": "w" * 64,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_code_verifier"


def test_unknown_redirect_uri_is_rejected(monkeypatch) -> None:
    _login_as(monkeypatch)
    _fake_provider(monkeypatch)
    local = TestClient(app)
    local.cookies.set("gal_session", "session")
    response = local.get(
        "/oauth/authorize?response_type=code&client_id=lthub"
        "&redirect_uri=https%3A%2F%2Fevil.test%2Fcb"
        f"&state=state-1&code_challenge={provider.pkce_challenge(VERIFIER)}"
        "&code_challenge_method=S256"
    )
    assert response.status_code == 400
    assert "invalid_redirect_uri" in response.text


def test_deny_redirects_with_access_denied(monkeypatch) -> None:
    _login_as(monkeypatch)
    _fake_provider(monkeypatch)
    local = TestClient(app)
    local.cookies.set("gal_session", "session")
    response = local.post(
        "/oauth/authorize",
        data={
            "client_id": "lthub",
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": provider.pkce_challenge(VERIFIER),
            "code_challenge_method": "S256",
            "state": "state-1",
            "decision": "deny",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=access_denied" in response.headers["location"]


def test_metadata_describes_provider(monkeypatch) -> None:
    response = client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    payload = response.json()
    assert payload["authorization_endpoint"].endswith("/oauth/authorize")
    assert payload["code_challenge_methods_supported"] == ["S256"]


def test_safe_next_only_accepts_local_paths() -> None:
    assert (
        _safe_next("/oauth/authorize?client_id=lthub")
        == "/oauth/authorize?client_id=lthub"
    )
    assert _safe_next("https://evil.test/") == "/"
    assert _safe_next("//evil.test/") == "/"
    assert _safe_next("/auth/login") == "/"
    assert _safe_next(None) == "/"
