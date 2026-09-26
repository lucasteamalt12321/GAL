from fastapi.testclient import TestClient

from app.config import Settings
from app.csrf import new_token
from app.main import app


def _patch_prod(monkeypatch) -> None:
    import app.middleware as middleware_module

    monkeypatch.setattr(
        middleware_module,
        "get_settings",
        lambda: Settings(app_env="production"),
    )


def test_csrf_unit_new_token_is_unique() -> None:
    assert new_token() != new_token()


def test_get_sets_csrf_cookie() -> None:
    fresh = TestClient(app)
    resp = fresh.get("/")
    assert resp.status_code == 200
    assert "gal_csrf" in resp.cookies


def test_forms_embed_csrf_token() -> None:
    fresh = TestClient(app)
    resp = fresh.get("/auth/login")
    embedded = resp.cookies.get("gal_csrf")
    assert embedded is not None
    assert 'name="_csrf"' in resp.text
    assert f'value="{embedded}"' in resp.text


def test_development_post_allowed_without_token() -> None:
    fresh = TestClient(app)
    resp = fresh.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303


def test_production_post_without_token_rejected(monkeypatch) -> None:
    _patch_prod(monkeypatch)
    prod_client = TestClient(app)
    prod_client.get("/")
    resp = prod_client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 403


def test_production_post_with_wrong_token_rejected(monkeypatch) -> None:
    _patch_prod(monkeypatch)
    prod_client = TestClient(app)
    prod_client.get("/")
    token = prod_client.cookies.get("gal_csrf")
    resp = prod_client.post(
        "/auth/logout",
        data={"_csrf": token + "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_production_post_with_form_token_allowed(monkeypatch) -> None:
    _patch_prod(monkeypatch)
    prod_client = TestClient(app)
    prod_client.get("/")
    token = prod_client.cookies.get("gal_csrf")
    resp = prod_client.post(
        "/auth/logout",
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_production_post_with_header_token_allowed(monkeypatch) -> None:
    _patch_prod(monkeypatch)
    prod_client = TestClient(app)
    prod_client.get("/")
    token = prod_client.cookies.get("gal_csrf")
    resp = prod_client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
