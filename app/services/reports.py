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

    # Дубль считаем только среди pending: partial-индекс reports_pending_unique
    # (008/245) явно разрешает новую жалобу после рассмотрения. limit(1)
    # вместо maybe_single: при ослабленном индексе maybe_single() на >1 строке
    # бросил бы APIError (406) -> 500.
    existing = (
        client.table("reports")
        .select("id,status")
        .eq("achievement_id", achievement_id)
        .eq("reporter_id", user.id)
        .eq("status", "pending")
        .limit(1)
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

    # Фильтр по status='pending' делает решение идемпотентным при гонке двух
    # модераторов: второй UPDATE не найдёт строк и получит внятную ошибку
    # вместо повторного accept (двойное скрытие + двойной пересчёт рангов).
    updated = (
        client.table("reports")
        .update(
            {
                "status": decision,
                "resolved_at": _now_iso(),
                "resolved_by": user.id,
                "resolution_reason": resolution_reason,
            }
        )
        .eq("id", report_id)
        .eq("status", "pending")
        .execute()
    )
    if updated is None or not updated.data:
        raise ReportError("Жалоба не найдена или уже рассмотрена.")

    if decision == "accepted":
        achievement_id = report.data.get("achievement_id")
        if achievement_id is not None:
            hidden = (
                client.table("achievements")
                .update({"status": "deleted"})
                .eq("id", achievement_id)
                .eq("status", "published")
                .execute()
            )
            if hidden is None or not hidden.data:
                logger.warning(
                    "Report %s accepted, but achievement %s was not published",
                    report_id,
                    achievement_id,
                )
            # Скрытое достижение выбывает из ранжирования: плотные ранги
            # пересчитываются, чтобы в лидерборде не оставалось дыр по рангу.
            # Ошибка не глотается: дыры в рангах видны всем в лидерборде.
            try:
                client.rpc("recompute_ranks").execute()
            except REPORTS_ERRORS:
                logger.warning(
                    "recompute_ranks failed after accepting report %s",
                    report_id,
                    exc_info=True,
                )
