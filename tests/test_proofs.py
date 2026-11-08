"""Загрузка доказательств: успешный путь и понятные ошибки.

Раньше любая ошибка Storage отдавалась пользователю как «Ошибка хранилища:
{'statusCode': 403, ...}» — по такому тексту нельзя понять, что делать.
Здесь фиксируем и сам путь загрузки файла, и перевод ошибок.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from storage3.exceptions import StorageApiError

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service

client = TestClient(app)

USER = auth_service.CurrentUser(
    id="22222222-2222-2222-2222-222222222222",
    email="user@gal.test",
    username="tester",
    display_name="Tester",
    avatar_url=None,
    role="user",
)


class _Result:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _ProofsTable:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.payload: dict | None = None

    def insert(self, payload: dict) -> _ProofsTable:
        self.payload = payload
        return self

    def execute(self) -> _Result:
        assert self.payload is not None
        self.rows.append(self.payload)
        return _Result([self.payload])


class _Bucket:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.uploads: list[tuple[str, bytes, dict]] = []
        self.removed: list[list[str]] = []

    def upload(self, path: str, content: bytes, options: dict) -> None:
        if self.error is not None:
            raise self.error
        self.uploads.append((path, content, options))

    def remove(self, paths: list[str]) -> None:
        self.removed.append(paths)


class _Client:
    def __init__(self, error: Exception | None = None) -> None:
        self.proofs = _ProofsTable()
        self.bucket = _Bucket(error)
        self.storage = SimpleNamespace(from_=lambda _name: self.bucket)

    def table(self, name: str) -> _ProofsTable:
        assert name == "proofs"
        return self.proofs


class _Uploaded:
    def __init__(
        self,
        data: bytes,
        filename: str = "shot.png",
        content_type: str = "image/png",
    ) -> None:
        self.file = io.BytesIO(data)
        self.filename = filename
        self.content_type = content_type


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user=USER))


def _attach(
    monkeypatch: pytest.MonkeyPatch,
    uploaded: _Uploaded | None,
    *,
    external_url: str = "",
    error: Exception | None = None,
) -> _Client:
    fake = _Client(error)
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _request: fake)
    completions_service.attach_proof(
        _request(),
        completion_id=7,
        achievement_id=42,
        description="",
        external_url=external_url,
        uploaded=uploaded,
    )
    return fake


def test_attach_file_uploads_to_own_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _attach(monkeypatch, _Uploaded(b"\x89PNG\r\n"))

    path, content, options = fake.bucket.uploads[0]
    assert content == b"\x89PNG\r\n"
    assert options["content-type"] == "image/png"
    assert options["upsert"] is False
    assert path.startswith(f"{USER.id}/42/")
    assert path.endswith("-shot.png")
    assert fake.proofs.rows[0]["proof_type"] == "image"
    assert fake.proofs.rows[0]["storage_path"] == path


def test_attach_link_skips_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _attach(monkeypatch, None, external_url="https://example.org/video")

    assert not fake.bucket.uploads
    assert fake.proofs.rows[0]["proof_type"] == "link"
    assert fake.proofs.rows[0]["storage_path"] == "https://example.org/video"


def test_attach_rejects_too_large_file(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = b"x" * (completions_service.MAX_REQUEST_PROOF_BYTES + 1)

    with pytest.raises(completions_service.ProofError) as excinfo:
        _attach(monkeypatch, _Uploaded(oversized))

    assert "4 МБ" in str(excinfo.value)


def test_storage_denied_message_is_user_friendly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = StorageApiError(
        "new row violates row-level security policy", "AccessDenied", 403
    )

    with pytest.raises(completions_service.ProofError) as excinfo:
        _attach(monkeypatch, _Uploaded(b"data"), error=error)

    message = str(excinfo.value)
    assert "Войдите" in message
    assert "row-level security" not in message
    assert "statusCode" not in message


def test_storage_payload_too_large_message(monkeypatch: pytest.MonkeyPatch) -> None:
    error = StorageApiError(
        "The object exceeded the maximum allowed size", "PayloadTooLarge", 413
    )

    with pytest.raises(completions_service.ProofError) as excinfo:
        _attach(monkeypatch, _Uploaded(b"data"), error=error)

    assert "4 МБ" in str(excinfo.value)


def test_complete_endpoint_accepts_multipart_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Реальный UploadFile из multipart доходит до Storage без потерь."""
    fake = _Client()
    item = {
        "id": 5,
        "title": "Piano song",
        "status": "published",
        "creator_id": "33333333-3333-3333-3333-333333333333",
    }

    monkeypatch.setattr(auth_service, "resolve_user", lambda _request: USER)
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _request: fake)
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda _request: [])
    monkeypatch.setattr(completions_service, "get_user_completion", lambda *_: None)
    monkeypatch.setattr(
        completions_service, "create_completion", lambda *_a, **_k: {"id": 7}
    )

    response = client.post(
        "/achievements/5/complete",
        files={"file": ("shot.png", b"\x89PNG\r\n", "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/achievements/5?info=submitted"
    path, content, options = fake.bucket.uploads[0]
    assert content == b"\x89PNG\r\n"
    assert options["content-type"] == "image/png"
    assert path.startswith(f"{USER.id}/5/")
