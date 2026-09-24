import logging
from datetime import datetime, timezone

import httpx
from fastapi import Request
from postgrest.exceptions import APIError
from storage3.exceptions import StorageException

from app.services import auth as auth_service
from app.services import completions as completions_service

logger = logging.getLogger(__name__)

MODERATION_ERRORS = (
    APIError,
    StorageException,
    httpx.HTTPError,
    auth_service.SupabaseNotConfiguredError,
)

PENDING_ACHIEVEMENTS = (
    "id,title,description,requirements,status,created_at,creator_id,"
    "category:categories(id,name,slug),"
    "creator:profiles!achievements_creator_id_fkey(id,username,display_name)"
)
PENDING_COMPLETIONS = (
    "id,achievement_id,user_id,status,created_at,"
    "achievement:achievements(id,title),"
    "user:profiles!achievement_completions_user_id_fkey(id,username,display_name),"
    "proofs(id,storage_path,proof_type,description,created_at)"
)


class ModerationError(Exception):
    pass


def list_pending_achievements(request: Request) -> list[dict]:
    client = auth_service.user_client_from_request(request)
    if client is None:
        return []
    res = (
        client.table("achievements")
        .select(PENDING_ACHIEVEMENTS)
        .eq("status", "pending")
        .order("created_at")
        .execute()
    )
    return res.data or []


def list_pending_completions(request: Request) -> list[dict]:
    client = auth_service.user_client_from_request(request)
    if client is None:
        return []
    res = (
        client.table("achievement_completions")
        .select(PENDING_COMPLETIONS)
        .eq("status", "pending")
        .order("created_at")
        .execute()
    )
    items = res.data or []
    for item in items:
        proofs = item.get("proofs") or []
        item["proof_views"] = [
            completions_service.proof_view(client, proof) for proof in proofs
        ]
    return items


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approve_creator_completion(client, achievement_id: int, moderator_id: str) -> None:
    ach = (
        client.table("achievements")
        .select("creator_id")
        .eq("id", achievement_id)
        .maybe_single()
        .execute()
    )
    if ach is None or not ach.data:
        return
    creator_id = ach.data["creator_id"]
    comp = (
        client.table("achievement_completions")
        .select("id,status")
        .eq("achievement_id", achievement_id)
        .eq("user_id", creator_id)
        .maybe_single()
        .execute()
    )
    if comp is None or not comp.data or comp.data["status"] != "pending":
        return
    client.table("achievement_completions").update(
        {"status": "approved", "approved_at": _now_iso()}
    ).eq("id", comp.data["id"]).execute()
    client.table("moderation_reviews").insert(
        {
            "completion_id": comp.data["id"],
            "moderator_id": moderator_id,
            "decision": "approved",
            "reason": "Creator completion auto-approved with achievement",
        }
    ).execute()


def decide_achievement(
    request: Request, achievement_id: int, decision: str, reason: str
) -> None:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise ModerationError("Требуется вход модератора.")
    if decision not in ("approved", "rejected"):
        raise ModerationError("Недопустимое решение.")
    reason = (reason or "").strip()

    updated = (
        client.table("achievements")
        .update({"status": "published" if decision == "approved" else "rejected"})
        .eq("id", achievement_id)
        .eq("status", "pending")
        .execute()
    )
    if not updated.data:
        raise ModerationError("Достижение не найдено или уже рассмотрено.")

    client.table("moderation_reviews").insert(
        {
            "achievement_id": achievement_id,
            "moderator_id": user.id,
            "decision": decision,
            "reason": reason,
        }
    ).execute()

    if decision == "approved":
        _approve_creator_completion(client, achievement_id, user.id)


def decide_completion(
    request: Request, completion_id: int, decision: str, reason: str
) -> None:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise ModerationError("Требуется вход модератора.")
    if decision not in ("approved", "rejected"):
        raise ModerationError("Недопустимое решение.")
    reason = (reason or "").strip()

    payload: dict = {"status": decision}
    if decision == "approved":
        payload["approved_at"] = _now_iso()
    updated = (
        client.table("achievement_completions")
        .update(payload)
        .eq("id", completion_id)
        .eq("status", "pending")
        .execute()
    )
    if not updated.data:
        raise ModerationError("Заявка не найдена или уже рассмотрена.")

    client.table("moderation_reviews").insert(
        {
            "completion_id": completion_id,
            "moderator_id": user.id,
            "decision": decision,
            "reason": reason,
        }
    ).execute()
