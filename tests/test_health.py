from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "GAL"
    assert "supabase" in data


def test_health_without_secrets() -> None:
    resp = client.get("/health")
    body = resp.text
    assert "SUPABASE" not in body
    assert "eyJ" not in body


def test_index_ok() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Global Achievement List" in resp.text


def test_static_css_ok() -> None:
    resp = client.get("/static/css/style.css")
    assert resp.status_code == 200
    assert "site-header" in resp.text
