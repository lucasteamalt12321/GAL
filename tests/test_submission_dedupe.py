"""D25: технический запрет абсолютно идентичных заявок.

Бизнес-правило живёт в БД (миграция 036): отпечаток считает PostgreSQL, а
приложение обязано отправлять полный набор доказательств одним RPC и понятно
объяснять отказ. Здесь проверяется именно контракт приложения с этой моделью:
детекторы ошибок, RPC-путь, очистка файлов, реконсиляция после потери ответа и
маппинг дублей в сервисах creation/top5/report.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service
from app.services import reports as reports_service
from app.services import submissions as submissions_service
from app.services import top5 as top5_service

client = TestClient(app)

USER = auth_service.CurrentUser(
    id="55555555-5555-5555-5555-555555555555",
    email="user@gal.test",
    username="tester",
    display_name="Tester",
    avatar_url=None,
    role="user",
)
ACHIEVEMENT_ID = 5


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user=USER))


class _Result:
    def __init__(self, data) -> None:
        self.data = data


class _Query:
    def __init__(self, data, error: Exception | None = None, single: bool = False):
        self._data = data
        self._error = error
        self._single = single

    def select(self, *_args, **_kwargs) -> _Query:
        return self

    def eq(self, *_args, **_kwargs) -> _Query:
        return self

    def limit(self, *_args, **_kwargs) -> _Query:
        return self

    def maybe_single(self) -> _Query:
        self._single = True
        return self

    def execute(self) -> _Result:
        if self._error is not None:
            raise self._error
        if self._single:
            return _Result(self._data[0] if self._data else None)
        return _Result(self._data)


class _ServiceClient:
    """Service-клиент с управляемым состоянием заявки и ответом RPC."""

    def __init__(
        self,
        *,
        state: dict | None = None,
        proof_rows: list[dict] | None = None,
        rpc_result=None,
        rpc_error: Exception | None = None,
        missing_table: bool = False,
        rpc_hook=None,
    ) -> None:
        self.state = state
        self.proof_rows = proof_rows or []
        self.rpc_result = rpc_result
        self.rpc_error = rpc_error
        self.missing_table = missing_table
        self.rpc_hook = rpc_hook
        self.rpc_calls: list[dict] = []

    def table(self, name: str) -> _Query:
        if name == "submission_fingerprints":
            if self.missing_table:
                return _Query(
                    [],
                    APIError(
                        {
                            "code": "42P01",
                            "message": "relation public.submission_fingerprints does not exist",
                        }
                    ),
                )
            return _Query([{"id": 1}])
        if name == "achievement_completions":
            rows = [self.state] if self.state is not None else []
            return _Query(rows, single=True)
        if name == "proofs":
            return _Query(list(self.proof_rows))
        raise AssertionError(f"unexpected table {name}")

    def rpc(self, name: str, params: dict) -> SimpleNamespace:
        assert name == "submit_completion_with_proofs"
        self.rpc_calls.append(params)
        service = self

        def _execute() -> _Result:
            if service.rpc_hook is not None:
                service.rpc_hook()
            if service.rpc_error is not None:
                raise service.rpc_error
            return _Result(service.rpc_result)

        return SimpleNamespace(execute=_execute)


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


class _UserClient:
    def __init__(self, upload_error: Exception | None = None) -> None:
        self.bucket = _Bucket(upload_error)
        self.storage = SimpleNamespace(from_=lambda _name: self.bucket)


def _upload(data: bytes = b"content", name: str = "shot.png") -> SimpleNamespace:
    return SimpleNamespace(
        file=io.BytesIO(data), filename=name, content_type="image/png"
    )


def _install(
    monkeypatch: pytest.MonkeyPatch, service: _ServiceClient, user_client: _UserClient
) -> None:
    monkeypatch.setattr(auth_service, "new_service_client", lambda: service)
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _request: user_client
    )


def _submit(
    monkeypatch: pytest.MonkeyPatch,
    service: _ServiceClient,
    user_client: _UserClient,
    **kwargs,
):
    _install(monkeypatch, service, user_client)
    return completions_service.submit_completion(
        _request(), achievement_id=ACHIEVEMENT_ID, **kwargs
    )


# +----------------------- детекторы ошибок БД -----------------------+


def test_duplicate_detectors_read_only_their_own_errors() -> None:
    duplicate = APIError(
        {"code": "23505", "message": "duplicate key value: DUPLICATE_SUBMISSION"}
    )
    assert submissions_service.is_duplicate_submission(duplicate)
    assert not submissions_service.is_duplicate_proof(duplicate)

    duplicate_proof = APIError(
        {"code": "23505", "message": "duplicate key: proofs_completion_content_unique"}
    )
    assert submissions_service.is_duplicate_proof(duplicate_proof)
    assert not submissions_service.is_duplicate_submission(duplicate_proof)

    unrelated = APIError({"code": "23505", "message": "another unique constraint"})
    assert not submissions_service.is_duplicate_submission(unrelated)
    assert not submissions_service.is_duplicate_proof(unrelated)
    assert submissions_service.is_already_completed(
        APIError({"message": "ALREADY_COMPLETED"})
    )


@pytest.mark.parametrize(
    ("marker", "message"),
    [
        ("PROOF_REQUIRED", submissions_service.PROOF_REQUIRED_MESSAGE),
        ("PROOF_HASH_REQUIRED", submissions_service.PROOF_HASH_REQUIRED_MESSAGE),
        ("INVALID_PROOF_PATH", submissions_service.INVALID_PROOF_PATH_MESSAGE),
        ("INVALID_PROOF_URL", submissions_service.INVALID_PROOF_URL_MESSAGE),
    ],
)
def test_proof_validation_errors_get_distinct_messages(
    marker: str, message: str
) -> None:
    exc = APIError({"code": "22023", "message": marker})
    assert submissions_service.proof_validation_error(exc) == message
    assert (
        submissions_service.proof_validation_error(APIError({"message": "other"}))
        is None
    )


# +--------------------------- RPC-путь ---------------------------+


def test_rpc_sends_full_proof_set_with_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _ServiceClient(rpc_result=[{"completion_id": 7, "attempt_no": 2}])
    user_client = _UserClient()

    result = _submit(
        monkeypatch,
        service,
        user_client,
        description="описание",
        external_url="https://example.org/run",
        uploads=[_upload(b"one"), _upload(b"two", "clip.mp4")],
        comment="note",
    )

    assert result == {"id": 7, "attempt_no": 2}
    assert len(service.rpc_calls) == 1
    params = service.rpc_calls[0]
    assert params["p_user_id"] == USER.id
    assert params["p_achievement_id"] == ACHIEVEMENT_ID
    assert params["p_comment"] == "note"
    proofs = params["p_proofs"]
    assert len(proofs) == 3
    assert proofs[0]["proof_type"] == "link"
    assert proofs[0].get("content_hash") is None
    assert all(
        len(proof["content_hash"]) == 64
        for proof in proofs
        if proof["proof_type"] != "link"
    )
    assert len(user_client.bucket.uploads) == 2


def test_rpc_duplicate_cleans_uploads_and_uses_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ServiceClient(
        rpc_error=APIError(
            {"code": "23505", "message": "duplicate key: DUPLICATE_SUBMISSION"}
        )
    )
    user_client = _UserClient()

    with pytest.raises(completions_service.DuplicateSubmissionError) as excinfo:
        _submit(
            monkeypatch,
            service,
            user_client,
            description="",
            external_url="",
            uploads=[_upload()],
        )

    assert str(excinfo.value) == submissions_service.DUPLICATE_SUBMISSION_MESSAGE
    assert any(
        path.startswith(USER.id)
        for paths in user_client.bucket.removed
        for path in paths
    )


@pytest.mark.parametrize(
    ("marker", "message"),
    [
        ("PROOF_REQUIRED", submissions_service.PROOF_REQUIRED_MESSAGE),
        ("PROOF_HASH_REQUIRED", submissions_service.PROOF_HASH_REQUIRED_MESSAGE),
        ("INVALID_PROOF_PATH", submissions_service.INVALID_PROOF_PATH_MESSAGE),
        ("INVALID_PROOF_URL", submissions_service.INVALID_PROOF_URL_MESSAGE),
    ],
)
def test_rpc_validation_error_is_not_masked_as_duplicate(
    monkeypatch: pytest.MonkeyPatch, marker: str, message: str
) -> None:
    service = _ServiceClient(
        rpc_error=APIError({"code": "22023", "message": f"raise exception {marker}"})
    )
    user_client = _UserClient()

    with pytest.raises(completions_service.ProofError) as excinfo:
        _submit(
            monkeypatch,
            service,
            user_client,
            description="",
            external_url="",
            uploads=[_upload()],
        )

    assert str(excinfo.value) == message


def test_submission_without_proof_is_rejected_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ServiceClient()
    user_client = _UserClient()

    with pytest.raises(completions_service.ProofError) as excinfo:
        _submit(
            monkeypatch,
            service,
            user_client,
            description="",
            external_url="",
            uploads=[],
        )

    assert str(excinfo.value) == submissions_service.PROOF_REQUIRED_MESSAGE
    assert service.rpc_calls == []


def test_missing_service_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    def _not_configured():
        raise auth_service.SupabaseNotConfiguredError("no key")

    monkeypatch.setattr(auth_service, "new_service_client", _not_configured)
    with pytest.raises(completions_service.CompletionError) as excinfo:
        completions_service.submit_completion(
            _request(),
            achievement_id=ACHIEVEMENT_ID,
            description="",
            external_url="https://example.org/run",
            uploads=[],
        )
    assert str(excinfo.value) == submissions_service.SERVICE_NOT_CONFIGURED_MESSAGE


def test_missing_migration_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ServiceClient(missing_table=True)
    monkeypatch.setattr(auth_service, "new_service_client", lambda: service)
    legacy_calls: list[dict] = []

    def _legacy(_request, **kwargs):
        legacy_calls.append(kwargs)
        return {"id": 99}

    monkeypatch.setattr(completions_service, "_submit_completion_legacy", _legacy)
    result = completions_service.submit_completion(
        _request(),
        achievement_id=ACHIEVEMENT_ID,
        description="",
        external_url="https://example.org/run",
        uploads=[],
    )

    assert result == {"id": 99}
    assert service.rpc_calls == []
    assert legacy_calls and legacy_calls[0]["achievement_id"] == ACHIEVEMENT_ID


def test_stale_schema_cache_after_migration_does_not_use_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ServiceClient(
        rpc_error=APIError(
            {
                "code": "PGRST202",
                "message": "Could not find function public.submit_completion_with_proofs",
            }
        )
    )
    user_client = _UserClient()
    monkeypatch.setattr(auth_service, "new_service_client", lambda: service)
    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _request: user_client
    )
    legacy_calls: list = []
    monkeypatch.setattr(
        completions_service,
        "_submit_completion_legacy",
        lambda *_a, **_k: legacy_calls.append(1),
    )

    with pytest.raises(completions_service.CompletionError) as excinfo:
        completions_service.submit_completion(
            _request(),
            achievement_id=ACHIEVEMENT_ID,
            description="",
            external_url="",
            uploads=[_upload()],
        )

    assert str(excinfo.value) == submissions_service.MIGRATION_PENDING_MESSAGE
    assert legacy_calls == []
    assert user_client.bucket.removed


def test_malformed_rpc_response_is_not_treated_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ServiceClient(rpc_result=[{"unexpected": 1}])
    user_client = _UserClient()

    with pytest.raises(completions_service.CompletionError) as excinfo:
        _submit(
            monkeypatch,
            service,
            user_client,
            description="",
            external_url="https://example.org/run",
            uploads=[],
        )

    assert "Не удалось отправить" in str(excinfo.value)


# +------------------- stale-файлы и гонки RPC -------------------+


def test_stale_proof_paths_are_read_with_service_role() -> None:
    service = _ServiceClient(
        state={"id": 11, "submission_attempt": 2, "status": "rejected"},
        proof_rows=[
            {"storage_path": "u/5/old.png", "proof_type": "image"},
            {"storage_path": "https://example.org/old", "proof_type": "link"},
        ],
    )
    assert completions_service._stale_proof_paths(service, USER.id, ACHIEVEMENT_ID) == (
        11,
        2,
        ["u/5/old.png"],
    )


def test_old_file_of_rejected_attempt_is_removed_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ServiceClient(
        state={"id": 11, "submission_attempt": 2, "status": "rejected"},
        proof_rows=[{"storage_path": "u/5/old.png", "proof_type": "image"}],
        rpc_result=[{"completion_id": 11, "attempt_no": 3}],
    )
    user_client = _UserClient()

    result = _submit(
        monkeypatch,
        service,
        user_client,
        description="",
        external_url="",
        uploads=[_upload()],
    )

    assert result == {"id": 11, "attempt_no": 3}
    assert user_client.bucket.removed == [["u/5/old.png"]]


def test_lost_rpc_response_after_commit_is_reconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"id": 11, "submission_attempt": 2, "status": "rejected"}
    service = _ServiceClient(
        state=state,
        proof_rows=[{"storage_path": "u/5/old.png", "proof_type": "image"}],
        rpc_error=httpx.ConnectError("response lost"),
    )
    user_client = _UserClient()

    def _commit_then_lose_response() -> None:
        new_path = user_client.bucket.uploads[0][0]
        state.update(status="pending", submission_attempt=3)
        service.proof_rows.append({"storage_path": new_path, "proof_type": "image"})

    service.rpc_hook = _commit_then_lose_response

    result = _submit(
        monkeypatch,
        service,
        user_client,
        description="",
        external_url="",
        uploads=[_upload()],
    )

    assert result == {"id": 11, "attempt_no": 3}
    assert user_client.bucket.removed == [["u/5/old.png"]]


def test_lost_rpc_response_before_commit_removes_orphan_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ServiceClient(
        state={"id": 11, "submission_attempt": 2, "status": "rejected"},
        proof_rows=[{"storage_path": "u/5/old.png", "proof_type": "image"}],
        rpc_error=httpx.ConnectError("request lost"),
    )
    user_client = _UserClient()

    with pytest.raises(completions_service.CompletionError):
        _submit(
            monkeypatch,
            service,
            user_client,
            description="",
            external_url="",
            uploads=[_upload()],
        )

    assert any(
        path.startswith(USER.id)
        for paths in user_client.bucket.removed
        for path in paths
    )
    # Старый файл отклонённой заявки удалять нельзя: строка proof ещё жива.
    assert not any(
        "old.png" in path for paths in user_client.bucket.removed for path in paths
    )


def test_failed_upload_cleans_its_path(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _ServiceClient()
    user_client = _UserClient(upload_error=httpx.ConnectError("storage lost"))

    with pytest.raises(completions_service.ProofError):
        _submit(
            monkeypatch,
            service,
            user_client,
            description="",
            external_url="",
            uploads=[_upload()],
        )

    assert any(
        path.startswith(USER.id)
        for paths in user_client.bucket.removed
        for path in paths
    )


# +--------------- маппинг дублей в других сервисах ---------------+


def test_achievement_create_maps_duplicate_to_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AchievementsTable:
        def insert(self, _payload):
            return self

        def execute(self):
            raise APIError(
                {"code": "23505", "message": "duplicate key: DUPLICATE_SUBMISSION"}
            )

    class _Client:
        def table(self, name: str):
            assert name == "achievements"
            return _AchievementsTable()

    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: True)
    monkeypatch.setattr(auth_service, "user_client_from_request", lambda _r: _Client())

    with pytest.raises(submissions_service.DuplicateSubmissionError) as excinfo:
        achievements_service.create_achievement(
            _request(),
            title="Заголовок",
            description="Описание",
            requirements="Требования",
            category_id=None,
        )
    assert str(excinfo.value) == submissions_service.DUPLICATE_SUBMISSION_MESSAGE


def test_top5_and_report_mappings_use_the_same_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = APIError(
        {"code": "23505", "message": "duplicate key: DUPLICATE_SUBMISSION"}
    )
    top5_error = top5_service._db_error(duplicate)
    assert str(top5_error) == submissions_service.DUPLICATE_SUBMISSION_MESSAGE

    class _ReportsTable:
        def __init__(self) -> None:
            self.inserted = False

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def insert(self, _payload):
            self.inserted = True
            return self

        def execute(self):
            if self.inserted:
                raise duplicate
            return _Result([])

    class _ReportClient:
        def __init__(self) -> None:
            self.table_obj = _ReportsTable()

        def table(self, name: str):
            assert name == "reports"
            return self.table_obj

    monkeypatch.setattr(
        auth_service, "user_client_from_request", lambda _r: _ReportClient()
    )
    with pytest.raises(reports_service.ReportError) as excinfo:
        reports_service.create_report(
            _request(),
            achievement_id=ACHIEVEMENT_ID,
            reason="Причина",
            description="Описание",
        )
    assert str(excinfo.value) == submissions_service.DUPLICATE_SUBMISSION_MESSAGE


# +------------------ откат достижения при отказе proof ------------------+


def test_failed_proof_rolls_back_just_created_achievement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой заявки не оставляет пользователя в тупике.

    Достижение откатывается, но форма создания остаётся заполненной: иначе
    автор попадал на страницу скрытого достижения, где формы отправки нет, и
    повторить отправку было нечем. Отпечаток достижения тоже снимается —
    иначе повторный отправ того же текста блокировался бы как дубль.
    """
    monkeypatch.setattr(
        auth_service,
        "resolve_user",
        lambda _request: USER,
    )
    monkeypatch.setattr(achievements_service, "has_user_evaluation", lambda _r: True)
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(
        achievements_service,
        "create_achievement",
        lambda *_a, **_k: {"id": 42},
    )
    monkeypatch.setattr(
        evaluation_service,
        "list_user_scale",
        lambda _r: [{"achievement_id": 1, "position": 1}],
    )

    def _reject_proof(*_args, **_kwargs):
        raise completions_service.ProofError(
            submissions_service.PROOF_HASH_REQUIRED_MESSAGE
        )

    monkeypatch.setattr(completions_service, "submit_completion", _reject_proof)
    rolled_back: list[int] = []
    monkeypatch.setattr(
        achievements_service,
        "soft_delete_own_pending",
        lambda _r, achievement_id: rolled_back.append(achievement_id),
    )
    dropped: list[int] = []
    monkeypatch.setattr(
        achievements_service,
        "drop_submission_fingerprint",
        lambda achievement_id: dropped.append(achievement_id),
    )

    response = client.post(
        "/achievements/create",
        data={
            "title": "Моё достижение",
            "description": "Описание",
            "requirements": "Требования",
            "harder_count": "1",
            "external_url": "https://example.org/proof",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert rolled_back == [42]
    assert dropped == [42]
    body = response.text
    assert "Не удалось вычислить отпечаток" in body
    # Поля формы сохранились — пользователь не должен вводить их заново.
    assert "Моё достижение" in body
    assert "Требования" in body


def test_constraint_errors_are_explained_to_user() -> None:
    """Ошибки целостности из БД не остаются безликим «Не удалось отправить»."""
    not_null = APIError(
        {
            "message": 'null value in column "description" of relation "proofs"',
            "code": "23502",
            "details": None,
            "hint": None,
        }
    )
    message = submissions_service.constraint_error(not_null)
    assert message is not None
    assert "Не удалось отправить заявку" not in message

    assert (
        submissions_service.constraint_error(
            APIError(
                {"message": "boom", "code": "PGRST999", "details": None, "hint": None}
            )
        )
        is None
    )


def test_unknown_database_error_still_gets_generic_text() -> None:
    """Неизвестный код не должен превращаться в подробности для пользователя."""
    exc = APIError(
        {"message": "unexpected", "code": "42P01", "details": None, "hint": None}
    )
    assert submissions_service.constraint_error(exc) is None


# +--------------------------- UI-подсказки ---------------------------+


def test_all_submission_forms_explain_identical_claim_block() -> None:
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    for name in (
        "achievements/create.html",
        "achievements/_proof_form.html",
        "achievements/detail.html",
        "submissions/list.html",
        "reports/report.html",
    ):
        text = (templates / name).read_text(encoding="utf-8").lower()
        assert "идентич" in text, f"{name} должен объяснять блокировку дублей"


def test_url_inputs_match_database_regex() -> None:
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    for name in ("achievements/create.html", "achievements/_proof_form.html"):
        text = (templates / name).read_text(encoding="utf-8")
        # HTML pattern регистрозависим, поэтому схема записана символьными
        # классами — так браузер и БД принимают одинаковый набор URL.
        assert r'pattern="[hH][tT][tT][pP][sS]?://\S+"' in text
