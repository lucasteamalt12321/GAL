import logging
import uuid
from pathlib import PurePosixPath

import httpx
from fastapi import Request
from postgrest.exceptions import APIError
from storage3.exceptions import StorageException
from supabase import Client

from app.services import auth as auth_service

logger = logging.getLogger(__name__)

PROOFS_BUCKET = "proofs"
MAX_PROOF_BYTES = 50 * 1024 * 1024
PROOF_URL_TTL = 3600

LINK_PROOF = "link"
IMAGE_PROOF = "image"
VIDEO_PROOF = "video"
DOCUMENT_PROOF = "document"
OTHER_PROOF = "other"

COMPLETION_ERRORS = (
    APIError,
    StorageException,
    httpx.HTTPError,
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
    cleaned = "".join(c for c in name if c.isalnum() or c in "._-")
    return cleaned[:80] or "proof"


def get_user_completion(request: Request, achievement_id: int) -> dict | None:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return None
    res = (
        client.table("achievement_completions")
        .select("id,achievement_id,user_id,status,created_at,approved_at")
        .eq("achievement_id", achievement_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if res is None:
        return None
    return res.data


def list_proofs(request: Request, completion_id: int) -> list[dict]:
    client = auth_service.client_for_request(request)
    res = (
        client.table("proofs")
        .select("id,storage_path,proof_type,description,created_at")
        .eq("completion_id", completion_id)
        .order("created_at")
        .execute()
    )
    return res.data or []


def proof_view(client: Client, proof: dict) -> dict:
    url = proof.get("storage_path")
    if proof.get("proof_type") != LINK_PROOF and url:
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


def create_completion(request: Request, achievement_id: int, user_id: str) -> dict:
    client = auth_service.user_client_from_request(request)
    if client is None:
        raise CompletionError("Требуется вход в систему.")
    try:
        res = (
            client.table("achievement_completions")
            .insert(
                {
                    "achievement_id": achievement_id,
                    "user_id": user_id,
                    "status": "pending",
                }
            )
            .execute()
        )
    except APIError as exc:
        if getattr(exc, "code", None) == "23505" or "duplicate" in str(exc).lower():
            raise AlreadyCompletedError(
                "Вы уже отправляли доказательство для этого достижения."
            ) from exc
        raise
    return res.data[0]


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

    if external_url:
        if not external_url.startswith(("http://", "https://")):
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
    content = uploaded.file.read() if uploaded is not None and filename else b""
    if not content:
        raise ProofError("Приложите файл или укажите ссылку на доказательство.")
    if len(content) > MAX_PROOF_BYTES:
        raise ProofError("Файл слишком большой (максимум 50 МБ).")

    content_type = getattr(uploaded, "content_type", None) or "application/octet-stream"
    path = f"{user.id}/{achievement_id}/{uuid.uuid4().hex}-{_safe_filename(filename)}"
    try:
        client.storage.from_(PROOFS_BUCKET).upload(
            path, content, {"content-type": content_type, "upsert": False}
        )
    except COMPLETION_ERRORS as exc:
        raise ProofError("Не удалось загрузить файл доказательства.") from exc

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


def submit_completion(
    request: Request,
    *,
    achievement_id: int,
    description: str,
    external_url: str,
    uploaded,
) -> dict:
    user = request.state.user
    if user is None:
        raise CompletionError("Требуется вход в систему.")
    if get_user_completion(request, achievement_id) is not None:
        raise AlreadyCompletedError(
            "Вы уже отправляли доказательство для этого достижения."
        )
    external_url = (external_url or "").strip()
    filename = getattr(uploaded, "filename", None)
    if not external_url and not filename:
        raise ProofError("Приложите файл или укажите ссылку на доказательство.")

    completion = create_completion(request, achievement_id, user.id)
    try:
        attach_proof(
            request,
            completion_id=completion["id"],
            achievement_id=achievement_id,
            description=description,
            external_url=external_url,
            uploaded=uploaded,
        )
    except CompletionError:
        logger.warning(
            "Proof attach failed for completion %s", completion["id"], exc_info=True
        )
        raise
    return completion
