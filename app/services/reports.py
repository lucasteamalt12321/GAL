"""GAL reports: жалобы на достижения + рассмотрение модератором.

Публичная подача (authenticated user, свой report, RLS reports_insert_own),
рассмотрение — модератор (reports_update_moderator). Принятие жалобы скрывает
достижение (status -> 'deleted'): публичный список фильтрует published.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from fastapi import Request
from postgrest.exceptions import APIError

from app.services import auth as auth_service

logger = logging.getLogger(__name__)

REPORTS_ERRORS = (
    APIError,
    httpx.HTTPError,
    auth_service.SupabaseNotConfiguredError,
)

QUEUE_SELECT = (
    "id,achievement_id,reporter_id,reason,description,status,created_at,"
    "achievement:achievements!reports_achievement_id_fkey(id,title,status),"
    "reporter:profiles!reports_reporter_id_fkey(id,username,display_name)"
)


class ReportError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_pending_reports(request: Request) -> list[dict]:
    client = auth_service.user_client_from_request(request)
    if client is None:
        return []
    res = (
        client.table("reports")
        .select(QUEUE_SELECT)
        .eq("status", "pending")
        .order("created_at")
        .execute()
    )
    return res.data or []


def create_report(
    request: Request,
    *,
    achievement_id: int,
    reason: str,
    description: str,
) -> None:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise ReportError("Требуется вход.")
    reason = (reason or "").strip()
    description = (description or "").strip()
    if not reason:
        raise ReportError("Укажите причину жалобы.")

    existing = (
        client.table("reports")
        .select("id,status")
        .eq("achievement_id", achievement_id)
        .eq("reporter_id", user.id)
        .maybe_single()
        .execute()
    )
    if existing is not None and existing.data:
        raise ReportError("Вы уже оставили жалобу на это достижение.")

    client.table("reports").insert(
        {
            "achievement_id": achievement_id,
            "reporter_id": user.id,
            "reason": reason,
            "description": description,
            "status": "pending",
        }
    ).execute()


def decide_report(
    request: Request,
    report_id: int,
    decision: str,
    resolution_reason: str,
) -> None:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise ReportError("Требуется вход модератора.")
    if decision not in ("accepted", "rejected"):
        raise ReportError("Недопустимое решение.")
    resolution_reason = (resolution_reason or "").strip()

    report = (
        client.table("reports")
        .select(QUEUE_SELECT)
        .eq("id", report_id)
        .maybe_single()
        .execute()
    )
    if report is None or not report.data or report.data.get("status") != "pending":
        raise ReportError("Жалоба не найдена или уже рассмотрена.")

    client.table("reports").update(
        {
            "status": decision,
            "resolved_at": _now_iso(),
            "resolved_by": user.id,
            "resolution_reason": resolution_reason,
        }
    ).eq("id", report_id).execute()

    if decision == "accepted":
        achievement_id = report.data.get("achievement_id")
        if achievement_id is not None:
            client.table("achievements").update({"status": "deleted"}).eq(
                "id", achievement_id
            ).eq("status", "published").execute()
