import logging
from datetime import UTC, datetime

import httpx
from fastapi import Request
from postgrest.exceptions import APIError
from storage3.exceptions import StorageException

from app.services import auth as auth_service
from app.services import bt_recompute
from app.services import completions as completions_service

logger = logging.getLogger(__name__)

MODERATION_ERRORS = (
    APIError,
    StorageException,
    httpx.HTTPError,
    auth_service.SupabaseNotConfiguredError,
)
MODERATOR_ROLES = ("moderator", "admin")
QUEUE_LIMIT = 100
HISTORY_LIMIT = 100

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
REVIEW_SELECT = (
    "id,completion_id,achievement_id,moderator_id,decision,reason,created_at"
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
        .limit(QUEUE_LIMIT)
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
        .limit(QUEUE_LIMIT)
        .execute()
    )
    items = res.data or []
    for item in items:
        proofs = item.get("proofs") or []
        item["proof_views"] = [
            completions_service.proof_view(client, proof) for proof in proofs
        ]
    return items


def list_history(request: Request) -> list[dict]:
    """Последние решения модераторов с расшифровкой целей."""
    client = auth_service.user_client_from_request(request)
    if client is None:
        return []
    res = (
        client.table("moderation_reviews")
        .select(REVIEW_SELECT)
        .order("created_at", desc=True)
        .limit(HISTORY_LIMIT)
        .execute()
    )
    reviews = res.data or []
    if not reviews:
        return []

    moderator_ids = {r["moderator_id"] for r in reviews if r.get("moderator_id")}
    achievement_ids = {r["achievement_id"] for r in reviews if r.get("achievement_id")}
    completion_ids = {r["completion_id"] for r in reviews if r.get("completion_id")}

    moderators = _titles_by_id(
        client, "profiles", "id,username,display_name", "id", moderator_ids
    )
    achievements = _titles_by_id(
        client, "achievements", "id,title", "id", achievement_ids
    )

    completions: dict[int, dict] = {}
    if completion_ids:
        cres = (
            client.table("achievement_completions")
            .select("id,achievement_id")
            .in_("id", list(completion_ids))
            .execute()
        )
        for row in cres.data or []:
            completions[row["id"]] = row
        missing_achievements = {
            row["achievement_id"]
            for row in completions.values()
            if row["achievement_id"] not in achievements
        }
        achievements.update(
            _titles_by_id(
                client, "achievements", "id,title", "id", missing_achievements
            )
        )

    history: list[dict] = []
    for review in reviews:
        moderator = moderators.get(review.get("moderator_id")) or {}
        if review.get("achievement_id"):
            target = achievements.get(review["achievement_id"]) or {}
            target_title = target.get("title")
            target_kind = "achievement"
        else:
            completion = completions.get(review.get("completion_id")) or {}
            target = achievements.get(completion.get("achievement_id")) or {}
            target_title = target.get("title")
            target_kind = "completion"
        history.append(
            {
                **review,
                "moderator": moderator,
                "target_title": target_title,
                "target_kind": target_kind,
            }
        )
    return history


def _titles_by_id(client, table: str, select: str, key: str, ids) -> dict:
    ids = [value for value in ids if value is not None]
    if not ids:
        return {}
    res = client.table(table).select(select).in_(key, ids).execute()
    return {row[key]: row for row in res.data or []}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _creator_completion(client, achievement_id: int, creator_id: str) -> dict | None:
    res = (
        client.table("achievement_completions")
        .select("id,status,proofs(id)")
        .eq("achievement_id", achievement_id)
        .eq("user_id", creator_id)
        .maybe_single()
        .execute()
    )
    if res is None:
        return None
    return res.data


def _approve_creator_completion(
    client, achievement_id: int, creator_id: str, moderator_id: str
) -> None:
    """Одобряет выполнение создателя до публикации достижения.

    Порядок важен: публикация без approved-completion запрещена deferred
    trigger-ом (миграция 013), поэтому завершение одобряется первым.
    """
    comp = _creator_completion(client, achievement_id, creator_id)
    if comp is None:
        raise ModerationError(
            "Нельзя опубликовать достижение: автор не приложил доказательство выполнения."
        )
    if not comp.get("proofs"):
        raise ModerationError(
            "Нельзя опубликовать достижение: у доказательства автора нет файла или ссылки."
        )
    if comp["status"] == "rejected":
        raise ModerationError(
            "Нельзя опубликовать достижение: доказательство автора было отклонено."
        )
    if comp["status"] == "approved":
        return
    client.table("achievement_completions").update(
        {"status": "approved", "approved_at": _now_iso()}
    ).eq("id", comp["id"]).execute()
    client.table("moderation_reviews").insert(
        {
            "completion_id": comp["id"],
            "moderator_id": moderator_id,
            "decision": "approved",
            "reason": "Creator completion auto-approved with achievement",
        }
    ).execute()


def _reject_creator_completion(
    client, achievement_id: int, creator_id: str, moderator_id: str, reason: str
) -> None:
    comp = _creator_completion(client, achievement_id, creator_id)
    if comp is None or comp["status"] != "pending":
        return
    client.table("achievement_completions").update({"status": "rejected"}).eq(
        "id", comp["id"]
    ).execute()
    client.table("moderation_reviews").insert(
        {
            "completion_id": comp["id"],
            "moderator_id": moderator_id,
            "decision": "rejected",
            "reason": reason or "Achievement rejected",
        }
    ).execute()


def _achievement_target(client, achievement_id: int) -> dict | None:
    res = (
        client.table("achievements")
        .select("creator_id,status")
        .eq("id", achievement_id)
        .maybe_single()
        .execute()
    )
    if res is None:
        return None
    return res.data


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
    target = _achievement_target(client, achievement_id)
    if target is None:
        raise ModerationError("Достижение не найдено.")
    if target.get("status") != "pending":
        raise ModerationError("Достижение не найдено или уже рассмотрено.")
    creator_id = target["creator_id"]

    if decision == "approved":
        _approve_creator_completion(client, achievement_id, creator_id, user.id)

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

    if decision == "rejected":
        _reject_creator_completion(client, achievement_id, creator_id, user.id, reason)

    # Публикация/отклонение меняет состав достижений в глобальной модели.
    bt_recompute.try_recompute_global_ratings()


def hide_achievement(request: Request, achievement_id: int, reason: str) -> None:
    """Скрывает опубликованное достижение (soft-delete) с записью в историю."""
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise ModerationError("Требуется вход модератора.")
    reason = (reason or "").strip()

    updated = (
        client.table("achievements")
        .update({"status": "deleted"})
        .eq("id", achievement_id)
        .in_("status", ["published", "pending"])
        .execute()
    )
    if not updated.data:
        raise ModerationError("Достижение не найдено или уже скрыто.")

    # Заявки на скрытое достижение не должны висеть в очереди модерации.
    client.table("achievement_completions").update({"status": "rejected"}).eq(
        "achievement_id", achievement_id
    ).eq("status", "pending").execute()

    client.table("moderation_reviews").insert(
        {
            "achievement_id": achievement_id,
            "moderator_id": user.id,
            "decision": "rejected",
            "reason": reason or "Achievement hidden by moderator",
        }
    ).execute()

    try:
        client.rpc("recompute_ranks").execute()
    except MODERATION_ERRORS:
        logger.warning(
            "recompute_ranks failed after hiding achievement %s",
            achievement_id,
            exc_info=True,
        )
    bt_recompute.try_recompute_global_ratings()


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
