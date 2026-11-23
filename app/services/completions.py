import logging
import uuid
from collections.abc import Callable
from pathlib import PurePosixPath

import httpx
from fastapi import Request
from postgrest.exceptions import APIError
from storage3.exceptions import StorageException
from supabase import Client

from app.config import get_settings
from app.services import auth as auth_service

logger = logging.getLogger(__name__)

PROOFS_BUCKET = "proofs"
MAX_PROOF_BYTES = 50 * 1024 * 1024
# Потолок длины комментария в БД (CHECK в миграции 035).
MAX_DB_COMMENT_LENGTH = 2000

# Суммарный лимит вложений одной заявки на прохождение: несколько файлов
# считаются вместе. Значение настраивается (MAX_ATTACHMENT_BYTES), потому что
# Vercel обрезает тело serverless-функции на ~4.5 МБ, а self-hosted Appwrite
# Functions такой потолок не имеют.
MAX_ATTACHMENT_BYTES = max(1024 * 1024, int(get_settings().max_attachment_bytes))
MAX_FILES_PER_COMPLETION = max(1, int(get_settings().max_files_per_completion))
# Потолок задаёт CHECK-ограничение в БД (миграция 035): выше 2000 символов
# заявка не сохранится, поэтому лимит приложения не может быть больше.
MAX_COMMENT_LENGTH = max(
    1, min(MAX_DB_COMMENT_LENGTH, int(get_settings().max_comment_length))
)

PROOF_URL_TTL = 3600

LINK_PROOF = "link"
IMAGE_PROOF = "image"
VIDEO_PROOF = "video"
DOCUMENT_PROOF = "document"
OTHER_PROOF = "other"


def _too_large_message(limit: int = MAX_ATTACHMENT_BYTES) -> str:
    mib = limit // (1024 * 1024)
    return (
        f"Суммарный объём вложений больше {mib} МБ. Приложите меньшие файлы "
        "или используйте ссылку на доказательство."
    )


def _file_too_large_message(limit: int = MAX_ATTACHMENT_BYTES) -> str:
    mib = limit // (1024 * 1024)
    return (
        f"Файл слишком большой: максимум {mib} МБ на один файл — для более "
        "крупных доказательств используйте ссылку."
    )


def _too_many_files_message(limit: int = MAX_FILES_PER_COMPLETION) -> str:
    return (
        f"Слишком много файлов: максимум {limit}. Объедините их в архив "
        "или приложите часть по ссылке."
    )


def _storage_error_message(exc: StorageException) -> str:
    """Переводит ответ Storage в понятное пользователю сообщение.

    Внутренние детали (RLS-политики, имена бакетов, статусы) не показываем:
    они бесполезны пользователю и раскрывают устройство хранилища.
    """
    status = getattr(exc, "status", None)
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = None
    code = str(getattr(exc, "code", "") or "")
    raw = f"{code} {getattr(exc, 'message', '') or exc}".lower()

    if status == 413 or any(
        marker in raw for marker in ("maximum allowed size", "too large", "file size")
    ):
        return _file_too_large_message()
    if status in (401, 403) or any(
        marker in raw
        for marker in (
            "jwt",
            "unauthorized",
            "access denied",
            "row-level security",
            "policy",
        )
    ):
        return (
            "Не удалось загрузить файл: нет доступа к хранилищу. "
            "Войдите в аккаунт заново и повторите или приложите ссылку."
        )
    if status == 404 or "bucket not found" in raw:
        return (
            "Хранилище доказательств временно недоступно. "
            "Попробуйте позже или приложите ссылку."
        )
    return (
        "Не удалось загрузить файл. Попробуйте ещё раз "
        "или приложите ссылку на доказательство."
    )


COMPLETION_ERRORS = (
    APIError,
    StorageException,
    httpx.HTTPError,
    httpx.InvalidURL,
    auth_service.SupabaseNotConfiguredError,
)


class CompletionError(Exception):
    pass


class AlreadyCompletedError(CompletionError):
    pass


class ProofError(CompletionError):
    pass


def _detect_proof_type(content_type: str | None, filename: str) -> str:
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return IMAGE_PROOF
    if ct.startswith("video/"):
        return VIDEO_PROOF
    name = (filename or "").lower()
    if ct == "application/pdf" or name.endswith(".pdf"):
        return DOCUMENT_PROOF
    return OTHER_PROOF


def _safe_filename(filename: str) -> str:
    name = PurePosixPath(filename or "proof").name
    # Разрешаем только латиницу, цифры и безопасные символы
    cleaned = "".join(c for c in name if c.isascii() and (c.isalnum() or c in "._-"))
    return cleaned[:80] or "proof"


def _is_missing_comment(exc: Exception) -> bool:
    """Ошибка «колонки comment ещё нет» (деплой раньше миграции 035)."""
    code = str(getattr(exc, "code", "") or "").upper()
    text = str(exc).lower()
    if code not in {"PGRST204", "42703"} and "column" not in text:
        return False
    return "comment" in text


def fetch_completions_with_comment(client: Client, base_fields: str, apply: Callable):
    """Выбирает строки прохождений, добавляя колонку comment, если она есть.

    До применения миграции 035 колонки ещё нет — запрос повторяется без неё,
    чтобы страницы не падали из-за неприменённой миграции.
    """

    def _run(fields: str):
        return apply(client.table("achievement_completions").select(fields)).execute()

    try:
        return _run(f"{base_fields},comment")
    except APIError as exc:
        if not _is_missing_comment(exc):
            raise
        return _run(base_fields)


def get_user_completion(request: Request, achievement_id: int) -> dict | None:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return None
    res = fetch_completions_with_comment(
        client,
        "id,achievement_id,user_id,status,created_at,approved_at,"
        "requested_harder_count",
        lambda query: (
            query.eq("achievement_id", achievement_id)
            .eq("user_id", user.id)
            .maybe_single()
        ),
    )
    if res is None:
        return None
    return res.data


def get_user_completion_by_id(request: Request, completion_id: int) -> dict | None:
    """Получить прохождение по ID (для владельца или модератора)."""
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return None
    res = fetch_completions_with_comment(
        client,
        "id,achievement_id,user_id,status,created_at,approved_at,"
        "requested_harder_count,"
        "user:profiles!user_id(username,display_name,avatar_url)",
        lambda query: query.eq("id", completion_id).maybe_single(),
    )
    if res is None:
        return None
    return res.data


def get_achievement_for_completion(request: Request, completion_id: int) -> dict | None:
    """Получить достижение по ID прохождения."""
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return None
    res = (
        client.table("achievement_completions")
        .select(
            "achievement:achievements(id,title,status,ranking_status,bt_rating,"
            "bt_strength,rank,average_position,evaluation_count,category:categories(id,name,slug))"
        )
        .eq("id", completion_id)
        .maybe_single()
        .execute()
    )
    if res is None or not res.data:
        return None
    return res.data.get("achievement")


def list_proofs(request: Request, completion_id: int) -> list[dict]:
    client = auth_service.client_for_request(request)
    res = (
        client.table("proofs")
        .select("id,storage_path,proof_type,description,created_at")
        .eq("completion_id", completion_id)
        .order("created_at")
        .order("id")
        .execute()
    )
    return res.data or []


def _signed_proof_url(client: Client, storage_path: str) -> str | None:
    try:
        signed = client.storage.from_(PROOFS_BUCKET).create_signed_url(
            storage_path, PROOF_URL_TTL
        )
    except COMPLETION_ERRORS:
        logger.debug("Failed to sign public proof %s", storage_path, exc_info=True)
        return None
    if not signed:
        return None
    return signed.get("signedURL") or signed.get("signedUrl")


def _decorate_public_completions(rows: list[dict]) -> list[dict]:
    """Добавляет ссылки к доказательствам публичных прохождений.

    Link-доказательства проходят проверку http(s); файлы подписываются
    service-role клиентом, потому что Storage RLS не пускает anon. Ошибка
    подписи не скрывает прохождение — доказательство остаётся без ссылки.
    """
    if not rows:
        return rows
    needs_signing = any(
        proof.get("proof_type") != LINK_PROOF
        for row in rows
        for proof in row.get("proofs") or []
    )
    service_client = None
    if needs_signing:
        try:
            service_client = auth_service.new_service_client()
        except COMPLETION_ERRORS:
            logger.debug("Service role unavailable for public proofs", exc_info=True)
    for row in rows:
        decorated = []
        for proof in row.get("proofs") or []:
            item = dict(proof)
            if item.get("proof_type") == LINK_PROOF:
                item["url"] = safe_external_url(item.get("storage_path"))
            elif service_client is not None and item.get("storage_path"):
                item["url"] = _signed_proof_url(service_client, item["storage_path"])
            else:
                item["url"] = None
            item.pop("storage_path", None)
            decorated.append(item)
        row["proofs"] = decorated
    return rows


def list_achievement_completers(request: Request, achievement_id: int) -> list[dict]:
    """Публичный список прошедших достижение (approved, профили и proofs).

    Читается через security definer RPC: RLS на achievement_completions
    разрешает пользователю только собственные строки. Ошибка RPC (например,
    миграция ещё не применена) не должна ломать карточку достижения.
    """
    try:
        client = auth_service.client_for_request(request)
        res = client.rpc(
            "achievement_completers", {"p_achievement_id": achievement_id}
        ).execute()
    except COMPLETION_ERRORS:
        logger.debug("Failed to load achievement completers", exc_info=True)
        return []
    return _decorate_public_completions(res.data or [])


def list_user_completed_achievements(request: Request, user_id: str) -> list[dict]:
    """Публичный список пройденных достижений игрока (approved, published)."""
    try:
        client = auth_service.client_for_request(request)
        res = client.rpc(
            "user_completed_achievements", {"p_user_id": user_id}
        ).execute()
    except COMPLETION_ERRORS:
        logger.debug("Failed to load user completed achievements", exc_info=True)
        return []
    return _decorate_public_completions(res.data or [])


def list_user_completions(request: Request, limit: int = 200) -> list[dict]:
    """Все заявки текущего пользователя (для раздела «Мои заявки»)."""
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return []
    res = fetch_completions_with_comment(
        client,
        "id,achievement_id,status,created_at,approved_at,"
        "achievement:achievements(id,title,status,creator_id,"
        "category:categories(name)),"
        "proofs(id,storage_path,proof_type,description,created_at)",
        lambda query: (
            query.eq("user_id", user.id).order("created_at", desc=True).limit(limit)
        ),
    )
    rows = res.data or []
    if not rows:
        return rows
    rejected_ids = [row["id"] for row in rows if row.get("status") == "rejected"]
    reasons = _latest_rejection_reasons(client, rejected_ids)
    approved_ids = [
        int(row["achievement_id"])
        for row in rows
        if row.get("status") == "approved" and row.get("achievement_id")
    ]
    evaluated: set[int] = set()
    if approved_ids:
        try:
            eval_res = (
                client.table("evaluations")
                .select("achievement_id")
                .eq("user_id", user.id)
                .in_("achievement_id", approved_ids)
                .execute()
            )
            evaluated = {int(row["achievement_id"]) for row in eval_res.data or []}
        except COMPLETION_ERRORS:
            evaluated = set()
    for row in rows:
        row["rejection_reason"] = reasons.get(row["id"])
        row["proof_count"] = len(row.get("proofs") or [])
        row["proof_views"] = decorate_proofs(request, row.get("proofs") or [])
        row["has_evaluation"] = int(row["achievement_id"]) in evaluated
    return rows


def list_approved_unevaluated(request: Request) -> list[dict]:
    """Подтверждённые выполнения без личной оценки (нужны как якоря шкалы)."""
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return []
    res = (
        client.table("achievement_completions")
        .select(
            "achievement_id,achievement:achievements(id,title,status,"
            "category:categories(name))"
        )
        .eq("user_id", user.id)
        .eq("status", "approved")
        .execute()
    )
    rows = [
        row
        for row in (res.data or [])
        if row.get("achievement") and row["achievement"].get("status") == "published"
    ]
    if not rows:
        return []
    ids = [int(row["achievement_id"]) for row in rows]
    try:
        eval_res = (
            client.table("evaluations")
            .select("achievement_id")
            .eq("user_id", user.id)
            .in_("achievement_id", ids)
            .execute()
        )
        evaluated = {int(row["achievement_id"]) for row in eval_res.data or []}
    except COMPLETION_ERRORS:
        evaluated = set()
    return [row for row in rows if int(row["achievement_id"]) not in evaluated]


def _latest_rejection_reasons(client, completion_ids: list[int]) -> dict[int, str]:
    if not completion_ids:
        return {}
    try:
        res = (
            client.table("moderation_reviews")
            .select("completion_id,decision,reason,created_at")
            .in_("completion_id", completion_ids)
            .order("created_at", desc=True)
            .execute()
        )
    except COMPLETION_ERRORS:
        return {}
    reasons: dict[int, str] = {}
    for row in res.data or []:
        completion_id = row.get("completion_id")
        if completion_id is None or completion_id in reasons:
            continue
        if row.get("decision") == "rejected":
            reasons[int(completion_id)] = row.get("reason") or ""
    return reasons


def get_rejection_reason(request: Request, completion_id: int) -> str | None:
    """Причина последнего отклонения заявки (видна только её владельцу/модератору)."""
    client = auth_service.user_client_from_request(request)
    if client is None:
        return None
    try:
        res = (
            client.table("moderation_reviews")
            .select("reason,decision")
            .eq("completion_id", completion_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except COMPLETION_ERRORS:
        return None
    if not res.data or res.data[0].get("decision") != "rejected":
        return None
    return res.data[0].get("reason") or None


def safe_external_url(value: str | None) -> str | None:
    """Оставляет только абсолютные http(s)-ссылки.

    Проверка на записи недостаточна: строку proofs можно вставить напрямую
    через PostgREST, а `javascript:`/`data:` в href выполнился бы в origin GAL
    при клике модератора. Поэтому схема проверяется и при рендере.
    """
    candidate = (value or "").strip()
    if not candidate:
        return None
    if not candidate.lower().startswith(("http://", "https://")):
        return None
    return candidate


def proof_view(client: Client, proof: dict) -> dict:
    url = proof.get("storage_path")
    if proof.get("proof_type") == LINK_PROOF:
        url = safe_external_url(url)
    elif url:
        try:
            signed = client.storage.from_(PROOFS_BUCKET).create_signed_url(
                url, PROOF_URL_TTL
            )
        except COMPLETION_ERRORS:
            signed = None
        if signed:
            url = signed.get("signedURL") or signed.get("signedUrl")
    return {**proof, "url": url}


def decorate_proofs(request: Request, proofs: list[dict]) -> list[dict]:
    client = auth_service.client_for_request(request)
    return [proof_view(client, p) for p in proofs]


def _is_missing_visibility(exc: Exception) -> bool:
    """Ошибка «колонки proofs_visible ещё нет» (деплой раньше миграции 028)."""
    code = str(getattr(exc, "code", "") or "").upper()
    text = str(exc).lower()
    return code in {"PGRST204", "42703"} or (
        "proofs_visible" in text and "column" in text
    )


def _update_completion(client: Client, completion_id: int, fields: dict) -> None:
    """Обновляет заявку; без миграции 028 опускает неизвестную колонку."""
    try:
        (
            client.table("achievement_completions")
            .update(fields)
            .eq("id", completion_id)
            .execute()
        )
        return
    except APIError as exc:
        if "proofs_visible" not in fields or not _is_missing_visibility(exc):
            raise
    logger.info("proofs_visible is not applied yet; updating completion without it")
    fallback = {key: value for key, value in fields.items() if key != "proofs_visible"}
    if fallback:
        (
            client.table("achievement_completions")
            .update(fallback)
            .eq("id", completion_id)
            .execute()
        )


def _missing_columns(exc: Exception) -> set[str]:
    """Какие из новых колонок ещё не существуют в БД (миграция не применена)."""
    code = str(getattr(exc, "code", "") or "").upper()
    text = str(exc).lower()
    if code not in {"PGRST204", "42703"} and "column" not in text:
        return set()
    return {name for name in ("proofs_visible", "comment") if name in text}


def _set_completion_comment(client: Client, completion_id: int, comment: str) -> None:
    """Записывает (или очищает) комментарий к прохождению.

    Пользователь не может обновлять achievement_completions под RLS, поэтому
    комментарий пишет security definer RPC (владелец, заявка pending).
    Пустая строка очищает прежний комментарий — иначе текст отклонённой
    заявки уехал бы вместе с новой. Если миграция ещё не применена, заявка
    сохраняется без комментария.
    """
    try:
        client.rpc(
            "set_completion_comment",
            {"p_completion_id": completion_id, "p_comment": comment or ""},
        ).execute()
    except COMPLETION_ERRORS:
        logger.info(
            "Completion comment is not applied yet (migration 035 pending)",
            exc_info=True,
        )


def create_completion(
    request: Request,
    achievement_id: int,
    user_id: str,
    harder_count: int | None = None,
    proofs_visible: bool = True,
    comment: str = "",
) -> dict:
    client = auth_service.user_client_from_request(request)
    if client is None:
        raise CompletionError("Требуется вход в систему.")
    payload = {
        "achievement_id": achievement_id,
        "user_id": user_id,
        "status": "pending",
    }
    if harder_count is not None:
        payload["requested_harder_count"] = harder_count
    if not proofs_visible:
        # true совпадает с default из миграции 028 и не требует новой колонки.
        payload["proofs_visible"] = False
    if comment:
        payload["comment"] = comment
    try:
        try:
            res = client.table("achievement_completions").insert(payload).execute()
        except APIError as exc:
            missing = _missing_columns(exc)
            missing &= set(payload)
            if not missing:
                raise
            logger.info(
                "Columns %s are not applied yet; creating completion without them",
                sorted(missing),
            )
            for name in missing:
                payload.pop(name, None)
            res = client.table("achievement_completions").insert(payload).execute()
    except APIError as exc:
        if getattr(exc, "code", None) == "23505" or "duplicate" in str(exc).lower():
            raise AlreadyCompletedError(
                "Вы уже отправляли доказательство для этого достижения."
            ) from exc
        raise
    return res.data[0]


def _uploaded_size(uploaded) -> int | None:
    """Размер загруженного файла без чтения содержимого в память.

    Starlette уже распарсил multipart и положил тело в SpooledTemporaryFile,
    поэтому seek/tell дают точный размер. Раньше файл читался целиком и только
    потом сравнивался с лимитом — тело произвольного размера оседало в RAM.
    """
    stream = getattr(uploaded, "file", None)
    if stream is None:
        return None
    try:
        current = stream.tell()
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(current, 0)
    except (OSError, ValueError):
        return None
    return int(size)


def attach_proof(
    request: Request,
    *,
    completion_id: int,
    achievement_id: int,
    description: str,
    external_url: str,
    uploaded,
) -> dict:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise CompletionError("Требуется вход в систему.")

    external_url = (external_url or "").strip()
    description = (description or "").strip()

    if uploaded is not None:
        try:
            uploaded.file.seek(0)
        except (OSError, ValueError):
            pass

    if external_url:
        external_url = safe_external_url(external_url)
        if external_url is None:
            raise ProofError("Ссылка должна начинаться с http:// или https://")
        res = (
            client.table("proofs")
            .insert(
                {
                    "completion_id": completion_id,
                    "storage_path": external_url,
                    "proof_type": LINK_PROOF,
                    "description": description,
                }
            )
            .execute()
        )
        return res.data[0]

    filename = getattr(uploaded, "filename", None)
    has_file = uploaded is not None and bool(filename)

    # Проверяем размер файла ДО его чтения в память
    size = None
    if has_file:
        size = _uploaded_size(uploaded)
        if size is None:
            raise ProofError("Не удалось определить размер файла.")
        if size > MAX_ATTACHMENT_BYTES or size > MAX_PROOF_BYTES:
            raise ProofError(_file_too_large_message())

    # Теперь читаем файл
    try:
        content = uploaded.file.read() if has_file else b""
    except OSError as exc:
        raise ProofError("Не удалось прочитать файл. Выберите его заново.") from exc

    if not content:
        raise ProofError("Приложите файл или укажите ссылку на доказательство.")

    content_type = getattr(uploaded, "content_type", None) or "application/octet-stream"
    path = f"{user.id}/{achievement_id}/{uuid.uuid4().hex}-{_safe_filename(filename)}"
    try:
        client.storage.from_(PROOFS_BUCKET).upload(
            path, content, {"content-type": content_type, "upsert": False}
        )
    except StorageException as exc:
        logger.warning("Proof upload rejected by storage: %s", exc, exc_info=True)
        raise ProofError(_storage_error_message(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning("Proof upload network error: %s", exc, exc_info=True)
        raise ProofError(
            "Не удалось загрузить файл: сервис хранения не отвечает. "
            "Попробуйте ещё раз или приложите ссылку."
        ) from exc
    except Exception as exc:  # пользователю нужен понятный текст, детали — в лог
        logger.exception("Unexpected proof upload failure")
        raise ProofError(
            "Не удалось загрузить файл доказательства. Попробуйте ещё раз "
            "или приложите ссылку."
        ) from exc

    try:
        res = (
            client.table("proofs")
            .insert(
                {
                    "completion_id": completion_id,
                    "storage_path": path,
                    "proof_type": _detect_proof_type(content_type, filename),
                    "description": description,
                }
            )
            .execute()
        )
    except COMPLETION_ERRORS as exc:
        try:
            client.storage.from_(PROOFS_BUCKET).remove([path])
        except COMPLETION_ERRORS:
            logger.debug("Failed to remove orphan proof object %s", path, exc_info=True)
        raise ProofError("Не удалось сохранить доказательство.") from exc
    return res.data[0]


def _attachment_files(uploads) -> list:
    """Оставляет только реально выбранные файлы.

    Multipart может прислать пустые элементы (например, если браузер отправил
    поле без файла), а у них нет ни имени, ни содержимого.
    """
    files = []
    for item in uploads or []:
        if item is None:
            continue
        if not getattr(item, "filename", None):
            continue
        files.append(item)
    return files


def collect_uploads(files, file=None) -> list:
    """Собирает вложения из поля multiple и старого одиночного поля file.

    Старые формы (и закешированные страницы) присылают один файл в поле
    `file`, новые — несколько в `files`. Оба варианта поддерживаются.
    """
    uploads = _attachment_files(files)
    uploads.extend(_attachment_files([file]))
    return uploads


def _validate_attachments(files: list) -> int:
    """Проверяет число файлов и суммарный размер; возвращает суммарный размер."""
    if len(files) > MAX_FILES_PER_COMPLETION:
        raise ProofError(_too_many_files_message())

    total = 0
    for item in files:
        size = _uploaded_size(item)
        if size is None:
            raise ProofError("Не удалось определить размер файла.")
        if size > MAX_ATTACHMENT_BYTES or size > MAX_PROOF_BYTES:
            raise ProofError(_file_too_large_message())
        total += size
    if total > MAX_ATTACHMENT_BYTES:
        raise ProofError(_too_large_message())
    return total


def attach_proofs(
    request: Request,
    *,
    completion_id: int,
    achievement_id: int,
    description: str,
    external_url: str,
    uploads,
) -> list[dict]:
    """Прикрепляет к прохождению ссылку и/или несколько файлов.

    Каждый файл становится отдельным доказательством: так модератор видит
    все приложенные материалы, а пользователь может прислать, например,
    скриншот и короткое видео одной отправкой. Суммарный объём ограничен
    MAX_ATTACHMENT_BYTES.
    """
    files = _attachment_files(uploads)
    _validate_attachments(files)

    proofs: list[dict] = []
    try:
        if external_url:
            proofs.append(
                attach_proof(
                    request,
                    completion_id=completion_id,
                    achievement_id=achievement_id,
                    description=description,
                    external_url=external_url,
                    uploaded=None,
                )
            )
        for item in files:
            proofs.append(
                attach_proof(
                    request,
                    completion_id=completion_id,
                    achievement_id=achievement_id,
                    description=description,
                    external_url="",
                    uploaded=item,
                )
            )
    except CompletionError:
        # Часть вложений уже загружена — откатываем их, чтобы не оставить
        # заявку с «половинными» доказательствами.
        cleanup_failed_proofs(request, completion_id, proofs)
        raise
    return proofs


def cleanup_failed_proofs(
    request: Request, completion_id: int, proofs: list[dict]
) -> None:
    """Откатывает частично загруженные доказательства.

    Если вложений несколько, сбой на третьем файле не должен оставить заявку
    с двумя доказательствами из четырёх: строки proofs удаляются, объекты
    Storage — тоже. Сама заявка остаётся pending, чтобы пользователь мог
    прикласть файлы заново.
    """
    if not proofs:
        return

    paths = [
        proof.get("storage_path")
        for proof in proofs
        if proof.get("storage_path") and proof.get("proof_type") != LINK_PROOF
    ]

    client = auth_service.user_client_from_request(request)
    proof_ids = [
        proof.get("id") for proof in proofs if proof.get("id") is not None
    ]
    removed = False
    if client is not None and proof_ids:
        try:
            client.rpc(
                "remove_completion_proofs",
                {"p_completion_id": completion_id, "p_proof_ids": proof_ids},
            ).execute()
            removed = True
        except COMPLETION_ERRORS:
            logger.info(
                "remove_completion_proofs is not applied yet; rolling back with service role",
                exc_info=True,
            )

    if not removed and proof_ids:
        # Миграция 035 ещё не применена — удаляем строки через service role.
        try:
            service_client = auth_service.new_service_client()
            (
                service_client.table("proofs")
                .delete()
                .eq("completion_id", completion_id)
                .in_("id", proof_ids)
                .execute()
            )
        except COMPLETION_ERRORS:
            logger.warning(
                "Failed to remove proof rows of completion %s",
                completion_id,
                exc_info=True,
            )

    if not paths:
        return
    client = auth_service.user_client_from_request(request)
    if client is None:
        return
    try:
        client.storage.from_(PROOFS_BUCKET).remove(paths)
    except COMPLETION_ERRORS:
        logger.warning(
            "Failed to remove uploaded proof objects %r", paths, exc_info=True
        )


def submit_completion(
    request: Request,
    *,
    achievement_id: int,
    description: str,
    external_url: str,
    uploaded=None,
    uploads=None,
    harder_count: int | None = None,
    proofs_visible: bool = True,
    comment: str = "",
) -> dict:
    user = request.state.user
    if user is None:
        raise CompletionError("Требуется вход в систему.")
    client = auth_service.user_client_from_request(request)
    if client is None:
        raise CompletionError("Требуется вход в систему.")

    comment = (comment or "").strip()
    if len(comment) > MAX_COMMENT_LENGTH:
        raise ProofError(
            f"Комментарий слишком длинный: максимум {MAX_COMMENT_LENGTH} символов."
        )

    existing = get_user_completion(request, achievement_id)

    completion: dict
    comment_stored = False
    stale_proof_paths: list[str] = []
    if existing is not None and existing["status"] == "rejected":
        stale_proof_paths = [
            proof["storage_path"]
            for proof in list_proofs(request, existing["id"])
            if not str(proof.get("storage_path", "")).startswith(
                ("http://", "https://")
            )
        ]
        try:
            res = client.rpc(
                "reset_rejected_completion", {"p_achievement_id": achievement_id}
            ).execute()
        except COMPLETION_ERRORS as exc:
            raise CompletionError(
                "Не удалось обновить заявку. Попробуйте ещё раз."
            ) from exc
        completion = {"id": res.data[0]["completion_id"]}
        fields = {"proofs_visible": proofs_visible}
        if harder_count is not None:
            fields["requested_harder_count"] = harder_count
        _update_completion(client, completion["id"], fields)
    elif (
        existing is not None
        and existing["status"] == "pending"
        and not list_proofs(request, existing["id"])
    ):
        # Незавершённая заявка без доказательств: даём прикрепить файл к ней,
        # иначе пользователь попадал в тупик (форма не показывалась, а повторная
        # отправка упиралась в AlreadyCompletedError).
        completion = existing
        fields = {"proofs_visible": proofs_visible}
        if harder_count is not None:
            fields["requested_harder_count"] = harder_count
        _update_completion(client, completion["id"], fields)
    elif existing is not None:
        raise AlreadyCompletedError(
            "Вы уже отправляли доказательство для этого достижения."
        )
    else:
        completion = create_completion(
            request,
            achievement_id,
            user.id,
            harder_count=harder_count,
            proofs_visible=proofs_visible,
            comment=comment,
        )
        # Комментарий записан вместе с заявкой. Если колонки ещё нет (миграция
        # 035 не применена), комментарий допишется через RPC ниже.
        comment_stored = (completion.get("comment") or "").strip() == comment

    external_url = (external_url or "").strip()
    files = _attachment_files(uploads) + _attachment_files([uploaded])
    if not external_url and not files:
        already_pending = completion.get("status") == "pending" or (
            existing is not None and existing["status"] == "rejected"
        )
        if already_pending:
            try:
                client.rpc(
                    "remove_empty_pending_completion",
                    {"p_completion_id": completion["id"]},
                ).execute()
            except COMPLETION_ERRORS:
                logger.debug(
                    "Failed to clean empty completion %s",
                    completion["id"],
                    exc_info=True,
                )
        raise ProofError("Приложите файл или укажите ссылку на доказательство.")

    try:
        attach_proofs(
            request,
            completion_id=completion["id"],
            achievement_id=achievement_id,
            description=description,
            external_url=external_url,
            uploads=files,
        )
    except CompletionError as exc:
        logger.warning(
            "Proof attach failed for completion %s: %s",
            completion["id"],
            str(exc),
            exc_info=True,
        )
        try:
            client.rpc(
                "remove_empty_pending_completion",
                {"p_completion_id": completion["id"]},
            ).execute()
        except COMPLETION_ERRORS:
            logger.debug(
                "Failed to clean empty completion %s", completion["id"], exc_info=True
            )
        raise

    if not comment_stored:
        # Заявка уже существовала: комментарий пишется (или очищается) через
        # RPC — прежний текст отклонённой заявки не должен уезжать дальше.
        _set_completion_comment(client, completion["id"], comment)

    if stale_proof_paths:
        # Старые файлы удаляем только после успешной загрузки нового
        # доказательства: иначе сбой загрузки оставлял заявку вообще без файлов.
        try:
            client.storage.from_(PROOFS_BUCKET).remove(stale_proof_paths)
        except COMPLETION_ERRORS:
            logger.debug(
                "Failed to remove stale proof objects %r",
                stale_proof_paths,
                exc_info=True,
            )
    return completion
