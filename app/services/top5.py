"""Заявки на предварительный рейтинг топ-5.

Обычные пользовательские оценки живут в ``evaluations`` и не смешиваются с
решением модератора. Здесь — отдельный сценарий:

1. автор опубликованного достижения подаёт заявку (позиция, доказательство,
   обоснование);
2. модератор вручную одобряет временное место (до третьей обычной оценки) или
   отклоняет заявку; место 1–6 он выбирает сам в форме решения;
3. ``bt_recompute`` пересчитывает временную силу, а вьюха
   ``public.public_achievements`` показывает общий топ со звёздочкой.

Схема 027 всё ещё содержит таблицы экспертных приглашений, но приложение их не
использует: независимая экспертиза временно отключена. Статус ``expert_review``
распознаётся как legacy-состояние, чтобы ранее созданные заявки не зависли.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from postgrest.exceptions import APIError

from app.services import auth as auth_service
from app.services import bt_recompute

logger = logging.getLogger(__name__)

MODERATOR_ROLES = ("moderator", "admin")
MAX_POSITION = 5
PERMANENT_EVALUATIONS = 3
MAX_JUSTIFICATION = 4000
MAX_EVIDENCE_URL = 2000
MAX_NOTE = 2000

ACTIVE_STATUSES = ("pending", "expert_review", "approved")
DECISION_STATUSES = ("approved", "rejected", "cancelled")
# Отменить ошибочное решение можно для любого статуса, где заявка ещё живёт.
REVOCABLE_STATUSES = ("pending", "expert_review", "approved", "rejected")


def _is_missing_schema(exc: APIError) -> bool:
    """Отличает отсутствие таблиц 027 от ошибки колонки или прав.

    PGRST204 (нет колонки) тоже упоминает таблицу и schema cache, поэтому
    такие ответы не считаем признаком неприменённой миграции.
    """
    code = str(getattr(exc, "code", "") or "").upper()
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'details', '')}".lower()
    if code == "PGRST205" or "pgrst205" in text:
        return True
    if code == "PGRST204" or "column" in text:
        return False
    mentions_schema = "top5_" in text or "achievement_temporary_ratings" in text
    return mentions_schema and any(
        marker in text
        for marker in ("could not find", "does not exist", "schema cache")
    )


TOP5_ERRORS = (
    APIError,
    httpx.HTTPError,
    auth_service.SupabaseNotConfiguredError,
)

APPLICATION_SELECT = (
    "id,achievement_id,applicant_id,proposed_position,justification,"
    "evidence_url,evidence_note,status,decided_by,decided_at,decision_reason,"
    "converted_at,created_at,updated_at,"
    "achievement:achievements!top5_applications_achievement_id_fkey("
    "id,title,status,rank,evaluation_count,bt_rating,bt_strength,"
    "ranking_status,creator_id),"
    "applicant:profiles!top5_applications_applicant_id_fkey("
    "id,username,display_name,avatar_url)"
)
EVALUATION_SELECT = (
    "id,application_id,evaluator_id,evaluator_type,estimated_position,note,"
    "created_at,updated_at,"
    "evaluator:profiles!top5_evaluations_evaluator_id_fkey("
    "id,username,display_name)"
)
EVENT_SELECT = (
    "id,application_id,actor_id,event_type,payload,created_at,"
    "actor:profiles!top5_events_actor_id_fkey(id,username,display_name)"
)

DB_MESSAGES = {
    "ACHIEVEMENT_NOT_FOUND": "Достижение не найдено.",
    "ACHIEVEMENT_NOT_PUBLISHED": "Заявку можно подать только на опубликованное достижение.",
    "ALREADY_PERMANENT": (
        "У достижения уже три обычные оценки — оно в постоянном рейтинге."
    ),
    "ALREADY_IN_TOP5": "Достижение уже занимает постоянное место в топ-5.",
    "NOT_AUTHOR": "Заявку может подать только автор достижения.",
    "SELF_DECISION": "Нельзя принимать решение по собственной заявке.",
    "REASON_REQUIRED": "Укажите причину.",
    "TERMINAL_APPLICATION": "Заявка уже закрыта и не может быть изменена.",
    "SYSTEM_ONLY": "Этот переход выполняет система.",
    "AUTHOR_CANNOT_DECIDE": "Автор может только отозвать заявку.",
    "NOT_ALLOWED": "Недостаточно прав.",
}


class Top5Error(Exception):
    pass


def _client(request: Request):
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise Top5Error("Требуется вход в аккаунт.")
    return user, client


def _db_error(exc: Exception) -> Top5Error:
    text = f"{getattr(exc, 'message', '')} {exc}"
    for code, message in DB_MESSAGES.items():
        if code in text:
            return Top5Error(message)
    if "23505" in text or "duplicate key" in text.lower():
        return Top5Error("Такая запись уже существует.")
    return Top5Error("Не удалось сохранить изменения. Попробуйте ещё раз.")


def parse_position(raw: object, *, maximum: int = MAX_POSITION) -> int | None:
    value = str(raw or "").strip()
    if not value.isascii() or not value.isdigit():
        return None
    number = int(value)
    if number < 1 or number > maximum:
        return None
    return number


def valid_evidence_url(raw: str) -> bool:
    value = (raw or "").strip()
    if not value or len(value) > MAX_EVIDENCE_URL:
        return False
    parsed = urlsplit(value)
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _achievement_row(client, achievement_id: int) -> dict | None:
    res = (
        client.table("achievements")
        .select(
            "id,title,creator_id,status,rank,evaluation_count,"
            "bt_rating,bt_strength,ranking_status"
        )
        .eq("id", achievement_id)
        .maybe_single()
        .execute()
    )
    if res is None:
        return None
    return res.data


def application_context(request: Request, achievement: dict) -> dict:
    """Что показывать на карточке достижения: заявка и доступность формы."""
    user = request.state.user
    context = {
        "can_apply": False,
        "apply_blocked_reason": None,
        "my_application": None,
    }
    if user is None:
        return context
    is_creator = str(user.id) == str(achievement.get("creator_id"))
    if not is_creator:
        context["apply_blocked_reason"] = "Заявку подаёт автор достижения."
        return context
    if achievement.get("status") != "published":
        context["apply_blocked_reason"] = "Достижение ещё не опубликовано."
        return context
    if int(achievement.get("evaluation_count") or 0) >= PERMANENT_EVALUATIONS:
        context["apply_blocked_reason"] = (
            "У достижения уже три оценки — оно в постоянном рейтинге."
        )
        return context
    # Эффективное место могло сдвинуться временной заявкой; ограничение
    # «уже в топ-5» относится только к постоянному месту.
    rank = achievement.get("permanent_rank", achievement.get("rank"))
    if rank is not None and int(rank) <= MAX_POSITION:
        context["apply_blocked_reason"] = (
            "Достижение уже занимает постоянное место в топ-5."
        )
        return context

    client = auth_service.user_client_from_request(request)
    if client is None:
        return context
    try:
        res = (
            client.table("top5_applications")
            .select("id,status,proposed_position,created_at,decision_reason")
            .eq("achievement_id", achievement["id"])
            .in_("status", list(ACTIVE_STATUSES))
            .limit(1)
            .execute()
        )
    except APIError as exc:
        # До применения миграции 027 карточка достижения должна работать.
        if _is_missing_schema(exc):
            return context
        raise
    active = (res.data or [None])[0]
    context["my_application"] = active
    context["can_apply"] = active is None
    if active is not None:
        context["apply_blocked_reason"] = "Заявка уже рассматривается."
    return context


def submit_application(
    request: Request,
    achievement_id: int,
    *,
    proposed_position: object,
    justification: str,
    evidence_url: str,
    evidence_note: str = "",
) -> dict:
    user, client = _client(request)
    position = parse_position(proposed_position)
    if position is None:
        raise Top5Error("Укажите место в топ-5 от 1 до 5.")
    justification = (justification or "").strip()
    evidence_url = (evidence_url or "").strip()
    evidence_note = (evidence_note or "").strip()
    if not justification:
        raise Top5Error("Обоснование обязательно.")
    if len(justification) > MAX_JUSTIFICATION:
        raise Top5Error("Обоснование слишком длинное.")
    if not valid_evidence_url(evidence_url):
        raise Top5Error("Приложите ссылку на доказательство (http или https).")
    if len(evidence_note) > MAX_NOTE:
        raise Top5Error("Комментарий к доказательству слишком длинный.")

    achievement = _achievement_row(client, achievement_id)
    if achievement is None:
        raise Top5Error("Достижение не найдено.")
    if str(achievement.get("creator_id")) != str(user.id):
        raise Top5Error(DB_MESSAGES["NOT_AUTHOR"])
    if achievement.get("status") != "published":
        raise Top5Error(DB_MESSAGES["ACHIEVEMENT_NOT_PUBLISHED"])
    if int(achievement.get("evaluation_count") or 0) >= PERMANENT_EVALUATIONS:
        raise Top5Error(DB_MESSAGES["ALREADY_PERMANENT"])
    rank = achievement.get("rank")
    if rank is not None and int(rank) <= MAX_POSITION:
        raise Top5Error(DB_MESSAGES["ALREADY_IN_TOP5"])

    try:
        active = (
            client.table("top5_applications")
            .select("id")
            .eq("achievement_id", achievement_id)
            .in_("status", list(ACTIVE_STATUSES))
            .limit(1)
            .execute()
        )
    except TOP5_ERRORS:
        active = None
    if active is not None and active.data:
        # Честное сообщение вместо ошибки уникального индекса.
        raise Top5Error("Заявка уже рассматривается.")

    try:
        res = (
            client.table("top5_applications")
            .insert(
                {
                    "achievement_id": achievement_id,
                    "applicant_id": user.id,
                    "proposed_position": position,
                    "justification": justification,
                    "evidence_url": evidence_url,
                    "evidence_note": evidence_note,
                }
            )
            .execute()
        )
    except TOP5_ERRORS as exc:
        raise _db_error(exc) from exc
    return (res.data or [{}])[0]


def cancel_application(request: Request, application_id: int) -> None:
    user, client = _client(request)
    try:
        res = (
            client.table("top5_applications")
            .update({"status": "cancelled"})
            .eq("id", application_id)
            .eq("applicant_id", user.id)
            .in_("status", ["pending", "expert_review"])
            .execute()
        )
    except TOP5_ERRORS as exc:
        raise _db_error(exc) from exc
    if not res.data:
        raise Top5Error("Заявка не найдена или уже рассмотрена.")
    bt_recompute.try_recompute_temporary_ratings()


def _read_rows(query) -> list[dict]:
    """Строки запроса или пусто, если миграция 027 ещё не применена."""
    try:
        res = query.execute()
    except APIError as exc:
        if _is_missing_schema(exc):
            logger.info("top5 schema is not applied yet; returning no rows")
            return []
        raise
    return res.data or []


def _read_row(query) -> dict | None:
    try:
        res = query.execute()
    except APIError as exc:
        if _is_missing_schema(exc):
            logger.info("top5 schema is not applied yet; returning no row")
            return None
        raise
    if res is None:
        return None
    return res.data


def list_my_applications(request: Request) -> list[dict]:
    user, client = _client(request)
    return _read_rows(
        client.table("top5_applications")
        .select(APPLICATION_SELECT)
        .eq("applicant_id", user.id)
        .order("created_at", desc=True)
        .limit(100)
    )


def get_application(request: Request, application_id: int) -> dict | None:
    _user, client = _client(request)
    return _read_row(
        client.table("top5_applications")
        .select(APPLICATION_SELECT)
        .eq("id", application_id)
        .maybe_single()
    )


def list_evaluations(request: Request, application_id: int) -> list[dict]:
    _user, client = _client(request)
    return _read_rows(
        client.table("top5_evaluations")
        .select(EVALUATION_SELECT)
        .eq("application_id", application_id)
        .order("created_at")
    )


def list_events(request: Request, application_id: int) -> list[dict]:
    _user, client = _client(request)
    return _read_rows(
        client.table("top5_events")
        .select(EVENT_SELECT)
        .eq("application_id", application_id)
        .order("created_at")
    )


def application_bundle(request: Request, application_id: int) -> dict | None:
    application = get_application(request, application_id)
    if application is None:
        return None
    user = request.state.user
    is_moderator = user is not None and user.role in MODERATOR_ROLES
    evaluations = list_evaluations(request, application_id)
    events = list_events(request, application_id)
    my_evaluation = next(
        (row for row in evaluations if str(row.get("evaluator_id")) == str(user.id)),
        None,
    )
    is_applicant = user is not None and str(user.id) == str(
        application.get("applicant_id")
    )
    status = application.get("status")
    can_decide = bool(
        is_moderator and not is_applicant and status in REVOCABLE_STATUSES
    )
    # RLS отдаёт заявителю голоса только после решения; иначе счётчик
    # выглядел бы нулём и вводил в заблуждение.
    evaluations_hidden = bool(
        is_applicant and not is_moderator and status in ("pending", "expert_review")
    )
    return {
        "application": application,
        "evaluations": evaluations,
        "events": events,
        "my_evaluation": my_evaluation,
        "is_moderator": is_moderator,
        "can_decide": can_decide,
        "can_cancel": bool(is_applicant and status in ("pending", "expert_review")),
        "evaluations_hidden": evaluations_hidden,
        "is_applicant": is_applicant,
    }


def moderation_queue(request: Request) -> list[dict]:
    user, client = _client(request)
    if user.role not in MODERATOR_ROLES:
        raise Top5Error("Недостаточно прав.")
    res = (
        client.table("top5_applications")
        .select(APPLICATION_SELECT)
        .in_("status", list(ACTIVE_STATUSES))
        .order("created_at")
        .limit(100)
        .execute()
    )
    applications = res.data or []
    if not applications:
        return []
    ids = [row["id"] for row in applications]
    evals = (
        client.table("top5_evaluations")
        .select("application_id,evaluator_id,evaluator_type")
        .in_("application_id", ids)
        .execute()
    )
    eval_rows: dict[int, list[dict]] = {}
    for row in evals.data or []:
        if row.get("evaluator_type") != "moderator":
            continue
        eval_rows.setdefault(int(row["application_id"]), []).append(row)
    for application in applications:
        votes = eval_rows.get(int(application["id"]), [])
        application["evaluation_count"] = len(votes)
    return applications


def try_moderation_queue(request: Request) -> list[dict]:
    """Очередь топ-5 для страницы модерации.

    До применения миграции 027 таблиц нет; остальная модерация должна
    продолжать работать, поэтому отсутствие схемы даёт пустой список.
    """
    try:
        return moderation_queue(request)
    except Top5Error:
        # Нет user-JWT клиента (офлайн-тесты, битая сессия): остальная
        # очередь модерации не должна падать из-за одного блока.
        logger.info("top5 queue unavailable without user client")
        return []
    except APIError as exc:
        if _is_missing_schema(exc):
            logger.info("top5 schema is not applied yet; queue is empty")
            return []
        raise


def decide_application(
    request: Request,
    application_id: int,
    *,
    decision: str,
    reason: str = "",
    estimated_position: object = "",
) -> None:
    user, client = _client(request)
    if user.role not in MODERATOR_ROLES:
        raise Top5Error("Недостаточно прав.")
    if decision not in DECISION_STATUSES:
        raise Top5Error("Недопустимое решение.")
    reason = (reason or "").strip()
    if decision == "rejected" and not reason:
        raise Top5Error(DB_MESSAGES["REASON_REQUIRED"])
    if len(reason) > MAX_NOTE:
        raise Top5Error("Причина слишком длинная.")

    application = get_application(request, application_id)
    if application is None:
        raise Top5Error("Заявка не найдена.")
    if str(application.get("applicant_id")) == str(user.id) and decision != "cancelled":
        raise Top5Error(DB_MESSAGES["SELF_DECISION"])

    position = None
    raw_position = str(estimated_position or "").strip()
    if raw_position:
        position = parse_position(raw_position, maximum=MAX_POSITION + 1)
        if position is None:
            raise Top5Error("Оценка места — от 1 до 6.")

    try:
        res = (
            client.table("top5_applications")
            .update({"status": decision, "decision_reason": reason})
            .eq("id", application_id)
            .execute()
        )
    except TOP5_ERRORS as exc:
        raise _db_error(exc) from exc
    if not res.data:
        raise Top5Error("Заявка не найдена.")

    if position is not None and decision == "approved":
        try:
            _save_evaluation(
                client,
                user,
                application,
                position,
                "Место назначено модератором",
            )
        except TOP5_ERRORS:
            # Решение уже сохранено; голос модератора — дополнительный вклад.
            logger.warning(
                "Moderator evaluation for application %s failed",
                application_id,
                exc_info=True,
            )

    bt_recompute.try_recompute_temporary_ratings()


def _evaluation_type(client, application_id: int, user_id) -> str:
    """Сохраняет тип уже записанной оценки.

    Триггер 027 запрещает менять тип и автора оценки. Если у модератора есть
    историческая запись с типом ``expert``, при повторном сохранении места
    оставляем исходный тип и обновляем только позицию.
    """
    res = (
        client.table("top5_evaluations")
        .select("evaluator_type")
        .eq("application_id", application_id)
        .eq("evaluator_id", user_id)
        .maybe_single()
        .execute()
    )
    if res is not None and res.data:
        return res.data["evaluator_type"]
    return ""


def _save_evaluation(
    client,
    user,
    application: dict,
    position: int,
    note: str,
) -> None:
    application_id = int(application["id"])
    evaluator_type = _evaluation_type(client, application_id, user.id) or "moderator"
    client.table("top5_evaluations").upsert(
        {
            "application_id": application_id,
            "evaluator_id": user.id,
            "evaluator_type": evaluator_type,
            "estimated_position": position,
            "note": note[:MAX_NOTE],
        },
        on_conflict="application_id,evaluator_id",
    ).execute()
