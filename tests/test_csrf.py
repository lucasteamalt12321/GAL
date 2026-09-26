from fastapi.testclient import TestClient

from app.config import Settings
from app.csrf import new_token
from app.main import app


def _patch_prod(monkeypatch) -> None:
    import app.csrf as csrf_module

    monkeypatch.setattr(
        csrf_module,
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


def _prod_client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


def test_development_post_allowed_without_token() -> None:
    fresh = TestClient(app)
    resp = fresh.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303


def test_production_post_without_token_rejected(monkeypatch) -> None:
    _patch_prod(monkeypatch)
    prod_client = _prod_client()
    prod_client.get("/")
    resp = prod_client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 403


def test_production_post_with_wrong_token_rejected(monkeypatch) -> None:
    _patch_prod(monkeypatch)
    prod_client = _prod_client()
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
    prod_client = _prod_client()
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
    prod_client = _prod_client()
    prod_client.get("/")
    token = prod_client.cookies.get("gal_csrf")
    resp = prod_client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_production_non_ascii_token_is_403_not_500(monkeypatch) -> None:
    _patch_prod(monkeypatch)
    prod_client = _prod_client()
    prod_client.get("/")
    token = prod_client.cookies.get("gal_csrf")
    resp = prod_client.post(
        "/auth/logout",
        data={"_csrf": token + "é"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_production_multipart_body_survives_csrf(monkeypatch) -> None:
    import io
    from typing import Annotated

    from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
    from fastapi.responses import JSONResponse

    from app.csrf import csrf_protect
    from app.middleware import UserContextMiddleware

    _patch_prod(monkeypatch)

    probe = FastAPI(dependencies=[Depends(csrf_protect)])
    probe.add_middleware(UserContextMiddleware)

    @probe.post("/upload", response_class=JSONResponse)
    async def upload(
        request: Request,
        title: Annotated[str, Form()],
        file: Annotated[UploadFile | None, File()] = None,
    ) -> JSONResponse:
        data = await file.read() if file is not None else None
        return JSONResponse(
            {
                "title": title,
                "file": file.filename if file is not None else None,
                "bytes": len(data) if data is not None else None,
            }
        )

    prod_client = TestClient(probe, base_url="https://testserver")
    prod_client.get("/upload")
    token = prod_client.cookies.get("gal_csrf")
    resp = prod_client.post(
        "/upload",
        data={"title": "hello", "_csrf": token},
        files={"file": ("proof.png", io.BytesIO(b"x" * 1024), "image/png")},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "hello"
    assert resp.json()["file"] == "proof.png"
    assert resp.json()["bytes"] == 1024
