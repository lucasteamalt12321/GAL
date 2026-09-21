from fastapi.testclient import TestClient

from app.main import app

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
