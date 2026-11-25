"""Несколько файлов и комментарий к прохождению.

Пользователь прикладывает к заявке на прохождение несколько файлов (суммарный
лимит 16 МБ) и пишет один комментарий ко всей заявке. Проверяем лимиты,
разбиение на отдельные доказательства, откат частично загруженных вложений и
запись комментария в документ прохождения.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service

client = TestClient(app)

USER = auth_service.CurrentUser(
    id="44444444-4444-4444-4444-444444444444",
    email="user@gal.test",
    username="tester",
    display_name="Tester",
    avatar_url=None,
    role="user",
)


class _Result:
    def __init__(self, data) -> None:
        self.data = data


class _ProofsTable:
    def __init__(self, errors: list[Exception] | None = None) -> None:
        self.rows: list[dict] = []
        self.errors = list(errors or [])

    def insert(self, payload: dict) -> _ProofsTable:
        self.payload = payload
        return self

    def execute(self) -> _Result:
        if self.errors:
            raise self.errors.pop(0)
        row = dict(self.payload, id=len(self.rows) + 1)
        self.rows.append(row)
        return _Result([row])


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
    """Клиент Supabase: только таблица proofs, Storage и вызовы RPC."""

    def __init__(self, error: Exception | None = None) -> None:
        self.proofs = _ProofsTable()
        self.bucket = _Bucket(error)
        self.storage = SimpleNamespace(from_=lambda _name: self.bucket)
        self.rpcs: list[tuple[str, dict]] = []

    def table(self, name: str) -> _ProofsTable:
        assert name == "proofs"
        return self.proofs

    def rpc(self, name: str, params: dict) -> SimpleNamespace:
        self.rpcs.append((name, params))
        return SimpleNamespace(execute=lambda: _Result(True))


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


def _attach_proofs(monkeypatch: pytest.MonkeyPatch, uploads, **kwargs):
    fake = _Client()
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: fake)
    proofs = completions_service.attach_proofs(
        _request(),
        completion_id=7,
        achievement_id=42,
        description=kwargs.pop("description", ""),
        external_url=kwargs.pop("external_url", ""),
        uploads=uploads,
        **kwargs,
    )
    return fake, proofs


def test_several_files_become_separate_proofs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, proofs = _attach_proofs(
        monkeypatch,
        [
            _Uploaded(b"\x89PNG\r\n", "one.png"),
            _Uploaded(b"\x89PNG\n", "two.png"),
            _Uploaded(b"%PDF", "report.pdf", "application/pdf"),
        ],
    )

    assert len(proofs) == 3
    assert len(fake.bucket.uploads) == 3
    assert [row["proof_type"] for row in fake.proofs.rows] == [
        "image",
        "image",
        "document",
    ]
    paths = {row["storage_path"] for row in fake.proofs.rows}
    assert len(paths) == 3
    assert all(path.startswith(f"{USER.id}/42/") for path in paths)


def test_link_and_files_can_be_sent_together(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, proofs = _attach_proofs(
        monkeypatch,
        [_Uploaded(b"\x89PNG\r\n", "one.png")],
        external_url="https://example.org/video",
    )

    assert len(proofs) == 2
    types = sorted(row["proof_type"] for row in fake.proofs.rows)
    assert types == ["image", "link"]
    assert len(fake.bucket.uploads) == 1


def test_total_size_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    half = completions_service.MAX_ATTACHMENT_BYTES // 2 + 1

    with pytest.raises(completions_service.ProofError) as excinfo:
        _attach_proofs(
            monkeypatch,
            [_Uploaded(b"x" * half, "a.bin"), _Uploaded(b"x" * half, "b.bin")],
        )

    assert "16 МБ" in str(excinfo.value)


def test_single_file_over_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = b"x" * (completions_service.MAX_ATTACHMENT_BYTES + 1)

    with pytest.raises(completions_service.ProofError) as excinfo:
        _attach_proofs(monkeypatch, [_Uploaded(oversized, "big.bin")])

    assert "16 МБ" in str(excinfo.value)


def test_too_many_files_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    limit = completions_service.MAX_FILES_PER_COMPLETION
    uploads = [_Uploaded(b"x", f"f{i}.bin") for i in range(limit + 1)]

    with pytest.raises(completions_service.ProofError) as excinfo:
        _attach_proofs(monkeypatch, uploads)

    assert "максимум" in str(excinfo.value)


def test_partial_failure_rolls_back_uploaded_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Client()
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: fake)

    def _upload(path: str, content: bytes, options: dict) -> None:
        if len(fake.bucket.uploads) >= 1:
            raise RuntimeError("storage is down")
        fake.bucket.uploads.append((path, content, options))

    fake.bucket.upload = _upload  # type: ignore[method-assign]

    with pytest.raises(completions_service.ProofError):
        completions_service.attach_proofs(
            _request(),
            completion_id=7,
            achievement_id=42,
            description="",
            external_url="",
            uploads=[
                _Uploaded(b"one", "one.png"),
                _Uploaded(b"two", "two.png"),
            ],
        )

    # Первое доказательство откачено: строка удалена, объект снят.
    assert (
        "remove_completion_proofs",
        {"p_completion_id": 7, "p_proof_ids": [1]},
    ) in fake.rpcs
    assert fake.bucket.removed


def test_comment_is_stored_with_new_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CompletionsTable:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        def insert(self, payload: dict) -> _CompletionsTable:
            self.payloads.append(dict(payload))
            return self

        def execute(self) -> _Result:
            return _Result([{"id": 42}])

    class _CompletionClient:
        def __init__(self) -> None:
            self.completions = _CompletionsTable()

        def table(self, name: str) -> _CompletionsTable:
            assert name == "achievement_completions"
            return self.completions

    fake = _CompletionClient()
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: fake)

    completions_service.create_completion(
        _request(), 42, USER.id, comment="Скриншот и видео"
    )

    assert fake.completions.payloads[0]["comment"] == "Скриншот и видео"


def test_comment_is_written_to_existing_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Заявка уже создана (повторная отправка) — комментарий пишется через RPC."""
    fake = _Client()
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: fake)
    monkeypatch.setattr(completions_service, "get_user_completion", lambda *_: None)
    monkeypatch.setattr(
        completions_service,
        "create_completion",
        lambda *_a, **_k: {"id": 7},
    )

    completions_service.submit_completion(
        _request(),
        achievement_id=42,
        description="",
        external_url="https://example.org/proof",
        uploads=[],
        comment="Доказательство из архива",
    )

    assert (
        "set_completion_comment",
        {"p_completion_id": 7, "p_comment": "Доказательство из архива"},
    ) in fake.rpcs


def test_long_comment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Client()
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: fake)

    with pytest.raises(completions_service.ProofError) as excinfo:
        completions_service.submit_completion(
            _request(),
            achievement_id=42,
            description="",
            external_url="https://example.org/proof",
            uploads=[],
            comment="x" * (completions_service.MAX_COMMENT_LENGTH + 1),
        )

    assert "слишком длинный" in str(excinfo.value).lower()


def _achievement() -> dict:
    return {
        "id": 5,
        "title": "Piano song",
        "description": "d",
        "requirements": "r",
        "status": "published",
        "rank": 1,
        "average_position": 1.5,
        "evaluation_count": 3,
        "bt_strength": 0.7,
        "bt_rating": 1123.4,
        "bt_comparisons": 5,
        "bt_rating_se": 21.0,
        "bt_rating_ci_low": 1082.0,
        "bt_rating_ci_high": 1164.0,
        "creator_id": "33333333-3333-3333-3333-333333333333",
        "category": {"id": 1, "name": "Музыка", "slug": "music"},
        "creator": {
            "id": "33333333-3333-3333-3333-333333333333",
            "username": "alice",
            "display_name": "Alice",
            "avatar_url": None,
        },
    }


def test_complete_endpoint_accepts_several_files_and_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Client()
    item = _achievement()

    monkeypatch.setattr(auth_service, "resolve_user", lambda _request: USER)
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: fake)
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda _r: [])
    monkeypatch.setattr(completions_service, "get_user_completion", lambda *_: None)
    monkeypatch.setattr(
        completions_service, "create_completion", lambda *_a, **_k: {"id": 7}
    )

    response = client.post(
        "/achievements/5/complete",
        files=[
            ("files", ("one.png", b"\x89PNG\r\n", "image/png")),
            ("files", ("two.png", b"\x89PNG\n", "image/png")),
        ],
        data={"comment": "Два скриншота"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/achievements/5?info=submitted"
    assert len(fake.bucket.uploads) == 2
    assert len(fake.proofs.rows) == 2
    assert (
        "set_completion_comment",
        {"p_completion_id": 7, "p_comment": "Два скриншота"},
    ) in fake.rpcs


def test_proof_form_offers_multiple_files_and_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _achievement()
    monkeypatch.setattr(auth_service, "resolve_user", lambda _request: USER)
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(evaluation_service, "list_user_scale", lambda _r: [])
    monkeypatch.setattr(
        completions_service, "list_achievement_completers", lambda *_: []
    )

    response = client.get("/achievements/5")

    assert response.status_code == 200
    body = response.text
    assert 'name="files"' in body
    assert "multiple" in body
    assert 'name="comment"' in body
    assert "16 МБ" in body
