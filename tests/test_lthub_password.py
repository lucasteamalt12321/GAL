"""Пароль LTHub-аккаунта в GAL.

Аккаунт, зарегистрированный через LTHub, входит в GAL по паролю LTHub:
GAL сверяет пароль с API LTHub и делает его паролем аккаунта.
"""

from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

import app.routers.auth as auth_router
from app.config import Settings
from app.main import app
from app.services import lthub_accounts

client = TestClient(app)


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _settings(**kwargs) -> Settings:
    base = {
        "app_env": "development",
        "lthub_api_base_url": "https://lthub.test",
        "lthub_password_check_enabled": True,
    }
    base.update(kwargs)
    return Settings(**base)


def test_login_endpoint_derived_from_base_url(monkeypatch) -> None:
    monkeypatch.setattr(
        lthub_accounts, "get_settings", lambda: _settings(lthub_login_url="")
    )
    assert lthub_accounts.login_endpoint() == "https://lthub.test/api/auth/login"


def test_login_endpoint_explicit_override(monkeypatch) -> None:
    monkeypatch.setattr(
        lthub_accounts,
        "get_settings",
        lambda: _settings(lthub_login_url="https://other.test/auth"),
    )
    assert lthub_accounts.login_endpoint() == "https://other.test/auth"


def test_verify_password_ok(monkeypatch) -> None:
    monkeypatch.setattr(lthub_accounts, "get_settings", lambda: _settings())
    captured: dict = {}

    def _post(url, json=None, timeout=None):
        captured.update({"url": url, "json": json})
        return _Response(200, {"login": "lucasteam", "token": "t", "user_id": 8})

    monkeypatch.setattr(lthub_accounts.httpx, "post", _post)
    result = lthub_accounts.verify_password(
        "lucasteam", "LucasTeam1", expected_subject="8"
    )
    assert result == lthub_accounts.PASSWORD_OK
    assert captured["json"] == {"login": "lucasteam", "password": "LucasTeam1"}


def test_verify_password_rejects_foreign_account(monkeypatch) -> None:
    monkeypatch.setattr(lthub_accounts, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        lthub_accounts.httpx,
        "post",
        lambda *_a, **_k: _Response(200, {"login": "other", "user_id": 99}),
    )
    result = lthub_accounts.verify_password(
        "lucasteam", "LucasTeam1", expected_subject="8"
    )
    assert result == lthub_accounts.PASSWORD_REJECTED


def test_verify_password_rejected_on_401(monkeypatch) -> None:
    monkeypatch.setattr(lthub_accounts, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        lthub_accounts.httpx,
        "post",
        lambda *_a, **_k: _Response(401, {"error": "Неверный логин или пароль"}),
    )
    assert (
        lthub_accounts.verify_password("lucasteam", "wrong")
        == lthub_accounts.PASSWORD_REJECTED
    )


def test_verify_password_unavailable_on_network_error(monkeypatch) -> None:
    monkeypatch.setattr(lthub_accounts, "get_settings", lambda: _settings())

    def _post(*_a, **_k):
        raise httpx.ConnectError("lthub down")

    monkeypatch.setattr(lthub_accounts.httpx, "post", _post)
    assert (
        lthub_accounts.verify_password("lucasteam", "LucasTeam1")
        == lthub_accounts.PASSWORD_UNAVAILABLE
    )


def test_verify_password_unavailable_on_server_error(monkeypatch) -> None:
    monkeypatch.setattr(lthub_accounts, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        lthub_accounts.httpx, "post", lambda *_a, **_k: _Response(503, {})
    )
    assert (
        lthub_accounts.verify_password("lucasteam", "LucasTeam1")
        == lthub_accounts.PASSWORD_UNAVAILABLE
    )


def test_verify_password_disabled_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        lthub_accounts,
        "get_settings",
        lambda: _settings(lthub_password_check_enabled=False),
    )
    assert (
        lthub_accounts.verify_password("lucasteam", "LucasTeam1")
        == lthub_accounts.PASSWORD_UNAVAILABLE
    )


def test_verify_password_requires_login_and_password(monkeypatch) -> None:
    monkeypatch.setattr(lthub_accounts, "get_settings", lambda: _settings())
    assert (
        lthub_accounts.verify_password("", "LucasTeam1")
        == lthub_accounts.PASSWORD_REJECTED
    )
    assert (
        lthub_accounts.verify_password("lucasteam", "")
        == lthub_accounts.PASSWORD_REJECTED
    )


def test_subjects_match_handles_numeric_and_text() -> None:
    assert lthub_accounts._subjects_match("8", "8")
    assert lthub_accounts._subjects_match(8, "08")
    assert lthub_accounts._subjects_match("abc", "ABC")
    assert not lthub_accounts._subjects_match("8", "9")
    assert not lthub_accounts._subjects_match("", "8")


class _ServiceStub:
    """Сервисный клиент: только auth.admin.update_user_by_id."""

    def __init__(self) -> None:
        self.updated: list[tuple[str, dict]] = []

        class _Admin:
            def __init__(self, outer: "_ServiceStub") -> None:
                self._outer = outer

            def update_user_by_id(self, user_id: str, payload: dict):
                self._outer.updated.append((user_id, payload))
                return SimpleNamespace(user=SimpleNamespace(id=user_id))

        self.auth = SimpleNamespace(admin=_Admin(self))


def test_mirror_password_sets_password_and_marks_synced(monkeypatch) -> None:
    service = _ServiceStub()
    marked: list[str] = []
    monkeypatch.setattr(
        lthub_accounts,
        "mark_password_synced",
        lambda _s, subject, synced=True: marked.append(subject),
    )
    assert lthub_accounts.mirror_password(service, "uid-1", "8", "LucasTeam1") is True
    assert service.updated == [("uid-1", {"password": "LucasTeam1"})]
    assert marked == ["8"]


def test_mirror_password_reports_failure(monkeypatch) -> None:
    class _Failing(_ServiceStub):
        def __init__(self) -> None:
            super().__init__()

            class _Admin:
                def update_user_by_id(self, *_a, **_k):
                    raise httpx.ConnectError("supabase down")

            self.auth = SimpleNamespace(admin=_Admin())

    assert lthub_accounts.mirror_password(_Failing(), "uid-1", "8", "x") is False


def _lthub_identity(**kwargs) -> dict:
    identity = {
        "profile_id": "uid-1",
        "subject": "8",
        "lthub_login": "lucasteam",
        "password_synced": False,
    }
    identity.update(kwargs)
    return identity


def _prepare_login(
    monkeypatch,
    *,
    identity,
    verify_result,
    mirror_calls,
    local_password: str | None = None,
):
    """Готовит вход: локальный пароль (local_password) и проверку пароля LTHub."""
    state = {"mirror": local_password}

    monkeypatch.setattr(
        auth_router.auth_service,
        "resolve_login_account",
        lambda _ident: ("uid-1", "lthub-oauth-8@example.com"),
    )

    class _AuthStub:
        def sign_in_with_password(self, payload):
            if payload["password"] != state["mirror"]:
                from supabase_auth.errors import AuthApiError

                raise AuthApiError(
                    message="Invalid login credentials",
                    status=400,
                    code="invalid_credentials",
                )
            return SimpleNamespace(
                session=SimpleNamespace(access_token="a.b.c", refresh_token="refresh")
            )

        def get_user(self, *_a, **_k):
            return SimpleNamespace(user=None)

        def refresh_session(self, *_a, **_k):
            return SimpleNamespace(session=None)

    monkeypatch.setattr(
        auth_router.auth_service,
        "new_anon_client",
        lambda: SimpleNamespace(auth=_AuthStub()),
    )
    monkeypatch.setattr(
        auth_router.auth_service,
        "new_service_client",
        lambda: SimpleNamespace(table=lambda _name: None),
    )
    monkeypatch.setattr(
        auth_router.lthub_accounts,
        "find_identity_for_login",
        lambda *_a, **_k: identity,
    )
    monkeypatch.setattr(
        auth_router.lthub_accounts, "verify_password", lambda *_a, **_k: verify_result
    )

    def _mirror(_service, profile_id, subject, password):
        state["mirror"] = password
        mirror_calls.append((profile_id, subject, password))
        return True

    monkeypatch.setattr(auth_router.lthub_accounts, "mirror_password", _mirror)


def test_login_uses_lthub_password_and_mirrors_it(monkeypatch) -> None:
    client.cookies.clear()
    mirror_calls: list[tuple] = []
    _prepare_login(
        monkeypatch,
        identity=_lthub_identity(),
        verify_result=lthub_accounts.PASSWORD_OK,
        mirror_calls=mirror_calls,
    )
    resp = client.post(
        "/auth/login",
        data={"email": "lucasteam", "password": "LucasTeam1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "gal_session=" in resp.headers["set-cookie"]
    assert mirror_calls == [("uid-1", "8", "LucasTeam1")]


def test_login_rejects_wrong_password_for_lthub_account(monkeypatch) -> None:
    client.cookies.clear()
    mirror_calls: list[tuple] = []
    _prepare_login(
        monkeypatch,
        identity=_lthub_identity(),
        verify_result=lthub_accounts.PASSWORD_REJECTED,
        mirror_calls=mirror_calls,
    )
    resp = client.post(
        "/auth/login",
        data={"email": "lucasteam", "password": "wrong-password"},
    )
    assert resp.status_code == 200
    assert "Неверный email или пароль" in resp.text
    assert mirror_calls == []
    assert "gal_session=" not in resp.headers.get("set-cookie", "")


def test_login_reports_lthub_unavailable(monkeypatch) -> None:
    client.cookies.clear()
    mirror_calls: list[tuple] = []
    _prepare_login(
        monkeypatch,
        identity=_lthub_identity(),
        verify_result=lthub_accounts.PASSWORD_UNAVAILABLE,
        mirror_calls=mirror_calls,
    )
    resp = client.post(
        "/auth/login",
        data={"email": "lucasteam", "password": "LucasTeam1"},
    )
    assert resp.status_code == 200
    assert "LTHub" in resp.text
    assert "gal_session=" not in resp.headers.get("set-cookie", "")


def test_login_without_lthub_identity_uses_local_password(monkeypatch) -> None:
    client.cookies.clear()
    mirror_calls: list[tuple] = []
    _prepare_login(
        monkeypatch,
        identity=None,
        verify_result=lthub_accounts.PASSWORD_REJECTED,
        mirror_calls=mirror_calls,
        local_password="LucasTeam1",
    )
    resp = client.post(
        "/auth/login",
        data={"email": "lucasteam", "password": "LucasTeam1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert mirror_calls == []


def test_oauth_login_keeps_confirmed_password(monkeypatch) -> None:
    """Подтверждённый пользователем пароль не перевыпускается при входе через LTHub."""
    import pytest

    updated: list[dict] = []

    class _Admin:
        def update_user_by_id(self, user_id, payload):
            updated.append(payload)
            return SimpleNamespace(user=SimpleNamespace(id=user_id))

    class _Service:
        auth = SimpleNamespace(admin=_Admin())

    service = _Service()
    monkeypatch.setattr(
        lthub_accounts.auth_service,
        "get_settings",
        lambda: Settings(
            app_env="development",
            supabase_url="https://test.supabase.co",
            supabase_service_role_key="test-key",
        ),
    )
    monkeypatch.setattr(
        lthub_accounts.auth_service, "new_service_client", lambda: service
    )
    monkeypatch.setattr(lthub_accounts, "_find_identity", lambda *_a, **_k: "uid-1")
    monkeypatch.setattr(lthub_accounts, "_sync_profile", lambda *_a, **_k: None)
    monkeypatch.setattr(
        lthub_accounts,
        "find_identity_by_profile",
        lambda *_a, **_k: _lthub_identity(password_synced=True),
    )
    monkeypatch.setattr(lthub_accounts, "store_lthub_login", lambda *_a, **_k: None)

    def _anon_client():
        raise httpx.ConnectError("supabase недоступен в тесте")

    monkeypatch.setattr(lthub_accounts.auth_service, "new_anon_client", _anon_client)

    with pytest.raises(lthub_accounts.LthubAccountError):
        # Сессию не создаём — интересует только отсутствие перевыпуска пароля.
        lthub_accounts.login_with_lthub(
            None, subject="8", login="lucasteam", display_name="Лука"
        )
    assert updated == []
