from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.services import auth as auth_service

client = TestClient(app)


def test_login_page_ok() -> None:
    resp = client.get("/auth/login")
    assert resp.status_code == 200
    assert 'name="email"' in resp.text
    assert 'name="password"' in resp.text


def test_register_page_ok() -> None:
    resp = client.get("/auth/register")
    assert resp.status_code == 200
    assert 'name="username"' in resp.text
    assert 'name="password"' in resp.text


def test_recover_page_ok() -> None:
    resp = client.get("/auth/recover")
    assert resp.status_code == 200
    assert 'action="/auth/recover"' in resp.text


def test_me_requires_auth() -> None:
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_logout_redirects_and_clears_cookies() -> None:
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_anonymous_nav_shows_auth_links() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Sign in" in resp.text
    assert "Register" in resp.text


def test_validate_username_rules() -> None:
    assert auth_service.validate_username("ab") is not None
    assert auth_service.validate_username("a" * 33) is not None
    assert auth_service.validate_username("bad name!") is not None
    assert auth_service.validate_username("") is not None
    assert auth_service.validate_username("alice_01") is None
    assert auth_service.validate_username("A.B-c") is None


def test_username_available_when_no_match(monkeypatch) -> None:
    class _Empty:
        data = None

    class _Query:
        def ilike(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return _Empty()

    class _Table:
        def select(self, *_a, **_k):
            return _Query()

    class _Client:
        def table(self, _name):
            return _Table()

    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _Client())
    assert auth_service.username_available("alice") is True


def test_username_available_when_taken(monkeypatch) -> None:
    class _Data:
        def __init__(self):
            self.data = [{"id": "123"}]

    class _Query:
        def ilike(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return _Data()

    class _Table:
        def select(self, *_a, **_k):
            return _Query()

    class _Client:
        def table(self, _name):
            return _Table()

    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _Client())
    assert auth_service.username_available("alice") is False


def test_register_short_username_shows_error() -> None:
    resp = client.post(
        "/auth/register",
        data={"username": "ab", "email": "x@example.com", "password": "secret1"},
    )
    assert resp.status_code == 200
    assert "от 3 до 32" in resp.text


def test_register_taken_username_shows_error(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_service, "username_available", lambda _username: False
    )
    resp = client.post(
        "/auth/register",
        data={"username": "alice", "email": "x@example.com", "password": "secret1"},
    )
    assert resp.status_code == 200
    assert "уже занято" in resp.text


def test_register_valid_proceeds_to_signup(monkeypatch) -> None:
    class _Session:
        session = None

    class _AuthStub:
        def sign_up(self, _payload):
            return _Session()

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_service, "username_available", lambda _u: True)
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post(
        "/auth/register",
        data={"username": "new_gal_user", "email": "u@example.com", "password": "secret1"},
    )
    assert resp.status_code == 200
    assert "Подтвердите email" in resp.text


def test_recover_passes_redirect_to(monkeypatch) -> None:
    import app.routers.auth as auth_router

    captured: list[dict] = []

    class _AuthStub:
        def reset_password_for_email(self, email, options=None):
            captured.append({"email": email, "options": options or {}})

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(
        auth_router, "get_settings", lambda: Settings(app_url="https://gal-inky.vercel.app")
    )
    monkeypatch.setattr(
        auth_service, "new_anon_client", lambda: _ClientStub()
    )
    resp = client.post("/auth/recover", data={"email": "user@example.com"})
    assert resp.status_code == 200
    assert captured[0]["email"] == "user@example.com"
    assert (
        captured[0]["options"].get("redirect_to")
        == "https://gal-inky.vercel.app/auth/login"
    )


def test_recover_without_app_url_omits_redirect_to(monkeypatch) -> None:
    import app.routers.auth as auth_router

    captured: list[dict] = []

    class _AuthStub:
        def reset_password_for_email(self, email, options=None):
            captured.append({"email": email, "options": options or {}})

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_router, "get_settings", lambda: Settings(app_url=""))
    monkeypatch.setattr(
        auth_service, "new_anon_client", lambda: _ClientStub()
    )
    resp = client.post("/auth/recover", data={"email": "user@example.com"})
    assert resp.status_code == 200
    assert "redirect_to" not in captured[0]["options"]


def test_register_accepts_json(monkeypatch) -> None:
    class _Session:
        session = None

    class _AuthStub:
        def sign_up(self, _payload):
            return _Session()

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_service, "username_available", lambda _u: True)
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post(
        "/auth/register",
        json={"username": "json_user", "email": "j@example.com", "password": "secret1"},
    )
    assert resp.status_code == 200
    assert "Подтвердите email" in resp.text


def test_login_accepts_json(monkeypatch) -> None:
    class _AuthStub:
        def sign_in_with_password(self, _payload):
            raise auth_service.AUTH_ERRORS[0](
                message="bad credentials", code="invalid_login"
            )

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post(
        "/auth/login",
        json={"email": "j@example.com", "password": "secret1"},
    )
    assert resp.status_code == 200
    assert "bad credentials" in resp.text


def test_recover_accepts_json(monkeypatch) -> None:
    import app.routers.auth as auth_router

    captured: list[dict] = []

    class _AuthStub:
        def reset_password_for_email(self, email, options=None):
            captured.append({"email": email, "options": options or {}})

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(
        auth_router, "get_settings", lambda: Settings(app_url="https://gal-inky.vercel.app")
    )
    monkeypatch.setattr(
        auth_service, "new_anon_client", lambda: _ClientStub()
    )
    resp = client.post("/auth/recover", json={"email": "user@example.com"})
    assert resp.status_code == 200
    assert captured[0]["email"] == "user@example.com"
