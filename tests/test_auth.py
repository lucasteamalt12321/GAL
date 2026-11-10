from types import SimpleNamespace

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


def test_register_page_preserves_next() -> None:
    resp = client.get("/auth/register", params={"next": "/achievements?tab=all"})
    assert resp.status_code == 200
    assert 'name="next" value="/achievements?tab=all"' in resp.text
    assert "intent=register" in resp.text


def test_register_redirects_to_next_after_signup(monkeypatch) -> None:
    class _Session:
        session = SimpleNamespace(access_token="access", refresh_token="refresh")

    class _AuthStub:
        def sign_up(self, _payload):
            return _Session()

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_service, "username_available", lambda _u: True)
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post(
        "/auth/register",
        data={
            "username": "new_gal_user",
            "email": "u@example.com",
            "password": "secret1",
            "next": "/achievements?tab=all",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements?tab=all"


def test_register_ignores_unsafe_next(monkeypatch) -> None:
    class _Session:
        session = SimpleNamespace(access_token="access", refresh_token="refresh")

    class _AuthStub:
        def sign_up(self, _payload):
            return _Session()

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_service, "username_available", lambda _u: True)
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post(
        "/auth/register",
        data={
            "username": "new_gal_user",
            "email": "u@example.com",
            "password": "secret1",
            "next": "//evil.test/path",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


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


def test_ilike_literal_escapes_wildcards() -> None:
    assert auth_service.ilike_literal("alice") == "alice"
    assert auth_service.ilike_literal("a%l_ic\\e") == "a\\%l\\_ic\\\\e"
    assert auth_service.ilike_literal("%_") == "\\%\\_"


def test_anonymous_nav_shows_auth_links() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Войти" in resp.text
    assert "Регистрация" in resp.text


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
    monkeypatch.setattr(auth_service, "username_available", lambda _username: False)
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
        data={
            "username": "new_gal_user",
            "email": "u@example.com",
            "password": "secret1",
        },
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
        auth_router,
        "get_settings",
        lambda: Settings(app_url="https://gal-inky.vercel.app"),
    )
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post("/auth/recover", data={"email": "user@example.com"})
    assert resp.status_code == 200
    assert captured[0]["email"] == "user@example.com"
    assert (
        captured[0]["options"].get("redirect_to")
        == "https://gal-inky.vercel.app/auth/update-password"
    )


def test_recover_without_app_url_uses_request_base(monkeypatch) -> None:
    import app.routers.auth as auth_router

    captured: list[dict] = []

    class _AuthStub:
        def reset_password_for_email(self, email, options=None):
            captured.append({"email": email, "options": options or {}})

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_router, "get_settings", lambda: Settings(app_url=""))
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post("/auth/recover", data={"email": "user@example.com"})
    assert resp.status_code == 200
    redirect_to = captured[0]["options"].get("redirect_to")
    assert redirect_to is not None
    assert redirect_to.endswith("/auth/update-password")


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
    from supabase_auth.errors import AuthApiError

    class _AuthStub:
        def sign_in_with_password(self, _payload):
            raise AuthApiError(
                message="Invalid login credentials",
                status=400,
                code="invalid_credentials",
            )

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post(
        "/auth/login",
        json={"email": "j@example.com", "password": "secret1"},
    )
    assert resp.status_code == 200
    assert "Неверный email или пароль" in resp.text


def test_recover_accepts_json(monkeypatch) -> None:
    import app.routers.auth as auth_router

    captured: list[dict] = []

    class _AuthStub:
        def reset_password_for_email(self, email, options=None):
            captured.append({"email": email, "options": options or {}})

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(
        auth_router,
        "get_settings",
        lambda: Settings(app_url="https://gal-inky.vercel.app"),
    )
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post("/auth/recover", json={"email": "user@example.com"})
    assert resp.status_code == 200
    assert captured[0]["email"] == "user@example.com"


def test_user_client_prefers_tokens_refreshed_by_resolve_user(monkeypatch) -> None:
    """После refresh клиент БД/Storage не должен брать старый access-токен."""
    captured: dict[str, str] = {}

    def _new_client(access: str, refresh: str):
        captured.update(access=access, refresh=refresh)
        return object()

    monkeypatch.setattr(auth_service, "new_user_client", _new_client)
    request = SimpleNamespace(
        cookies={
            auth_service.SESSION_COOKIE: "old.access.token",
            auth_service.REFRESH_COOKIE: "old-refresh",
        },
        state=SimpleNamespace(
            access_token="new.access.token",
            refresh_token="new-refresh",
        ),
    )

    assert auth_service.user_client_from_request(request) is not None
    assert captured == {"access": "new.access.token", "refresh": "new-refresh"}


def test_validate_email_rules() -> None:
    assert auth_service.validate_email("") is not None
    assert auth_service.validate_email("tony") is not None
    assert auth_service.validate_email("tony@gmail") is not None
    assert auth_service.validate_email("tony @gmail.com") is not None
    assert auth_service.validate_email("a@b.c") is None
    assert auth_service.validate_email("tonybig1120@gmail.com") is None


def test_friendly_auth_error_rate_limit_with_seconds() -> None:
    from supabase_auth.errors import AuthApiError

    exc = AuthApiError(
        message="For security purposes, you can only request this after 20 seconds.",
        status=429,
        code="over_email_send_rate_limit",
    )
    assert (
        auth_service.friendly_auth_error(exc)
        == "Слишком много попыток. Повторите через 20 сек."
    )


def test_friendly_auth_error_invalid_email() -> None:
    from supabase_auth.errors import AuthApiError

    exc = AuthApiError(
        message="Unable to validate email address: invalid format",
        status=400,
        code="validation_failed",
    )
    assert "Некорректный email" in auth_service.friendly_auth_error(exc)


def test_friendly_auth_error_email_exists() -> None:
    from supabase_auth.errors import AuthApiError

    exc = AuthApiError(
        message="User already registered", status=400, code="email_exists"
    )
    assert "уже зарегистрирован" in auth_service.friendly_auth_error(exc)


def test_friendly_auth_error_unknown_is_generic() -> None:
    from supabase_auth.errors import AuthApiError

    exc = AuthApiError(message="something odd", status=500, code="unexpected_failure")
    assert auth_service.friendly_auth_error(exc) == (
        "Не удалось выполнить запрос. Проверьте данные и попробуйте ещё раз."
    )


def test_register_invalid_email_shows_error() -> None:
    resp = client.post(
        "/auth/register",
        data={"username": "tony", "email": "not-an-email", "password": "secret1"},
    )
    assert resp.status_code == 200
    assert "Некорректный email" in resp.text


def test_register_autoconfirm_session_logs_in(monkeypatch) -> None:
    class _Session:
        access_token = "access.jwt.token"
        refresh_token = "refresh-token"

    class _Result:
        session = _Session()

    class _AuthStub:
        def sign_up(self, _payload):
            return _Result()

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(auth_service, "username_available", lambda _u: True)
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post(
        "/auth/register",
        data={
            "username": "tony",
            "email": "tony@example.com",
            "password": "secret1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "gal_session=" in resp.headers["set-cookie"]
    assert "gal_refresh=" in resp.headers["set-cookie"]


def test_resolve_login_email_passthrough_and_empty() -> None:
    assert auth_service.resolve_login_email("Tony@Example.com") == "Tony@Example.com"
    assert auth_service.resolve_login_email("  a@b.c  ") == "a@b.c"
    assert auth_service.resolve_login_email("") is None
    assert auth_service.resolve_login_email("   ") is None


def test_resolve_login_email_username_without_service_role(monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "get_settings", lambda: Settings(_env_file=None))
    assert auth_service.resolve_login_email("TonyBig") is None


def _service_settings() -> Settings:
    return Settings(
        supabase_url="https://x.supabase.co", supabase_service_role_key="key"
    )


def test_resolve_login_email_username_found(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class _Row:
        def __init__(self):
            self.data = [{"id": "uid-1"}]

    class _Query:
        def ilike(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def execute(self):
            return _Row()

    class _Table:
        def select(self, *_a, **_k):
            return _Query()

    class _User:
        email = "tonybig11200@gmail.com"

    class _Resp:
        user = _User()

    class _Admin:
        def get_user_by_id(self, uid):
            captured["uid"] = uid
            return _Resp()

    class _Auth:
        admin = _Admin()

    class _Service:
        auth = _Auth()

        def table(self, name):
            captured["table"] = name
            return _Table()

    monkeypatch.setattr(auth_service, "get_settings", _service_settings)
    monkeypatch.setattr(auth_service, "new_service_client", lambda: _Service())
    assert auth_service.resolve_login_email("TonyBig") == "tonybig11200@gmail.com"
    assert captured == {"uid": "uid-1", "table": "profiles"}


def test_resolve_login_email_username_not_found(monkeypatch) -> None:
    class _Row:
        def __init__(self):
            self.data = []

    class _Query:
        def ilike(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def execute(self):
            return _Row()

    class _Table:
        def select(self, *_a, **_k):
            return _Query()

    class _Service:
        def table(self, _name):
            return _Table()

    monkeypatch.setattr(auth_service, "get_settings", _service_settings)
    monkeypatch.setattr(auth_service, "new_service_client", lambda: _Service())
    assert auth_service.resolve_login_email("ghost") is None


def test_login_by_username_resolves_and_logs_in(monkeypatch) -> None:
    client.cookies.clear()
    captured: dict[str, str] = {}

    class _Session:
        access_token = "access.jwt.token"
        refresh_token = "refresh-token"

    class _Result:
        session = _Session()

    class _AuthStub:
        def sign_in_with_password(self, payload):
            captured.update(payload)
            return _Result()

    class _ClientStub:
        auth = _AuthStub()

    monkeypatch.setattr(
        auth_service,
        "resolve_login_email",
        lambda ident: "tonybig11200@gmail.com" if ident == "TonyBig" else None,
    )
    monkeypatch.setattr(auth_service, "new_anon_client", lambda: _ClientStub())
    resp = client.post(
        "/auth/login",
        data={"email": "TonyBig", "password": "secret1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert captured["email"] == "tonybig11200@gmail.com"
    assert "gal_session=" in resp.headers["set-cookie"]


def test_login_unknown_username_generic_error(monkeypatch) -> None:
    client.cookies.clear()
    monkeypatch.setattr(auth_service, "resolve_login_email", lambda _ident: None)
    resp = client.post("/auth/login", data={"email": "ghost", "password": "secret1"})
    assert resp.status_code == 200
    assert "Неверный email или пароль" in resp.text
