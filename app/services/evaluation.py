import httpx
from fastapi import Request
from postgrest.exceptions import APIError

from app.services import auth as auth_service
from app.services import completions as completions_service

EVALUATION_ERRORS = (
    APIError,
    httpx.HTTPError,
    auth_service.SupabaseNotConfiguredError,
)

EVALUATION_SELECT = (
    "id,achievement_id,position,locked,created_at,updated_at,"
    "achievement:achievements(id,title,category:categories(name))"
)


class EvaluationError(Exception):
    pass


class AuthRequiredError(EvaluationError):
    pass


class NotApprovedError(EvaluationError):
    pass


class LockedError(EvaluationError):
    pass


def get_user_evaluation(request: Request, achievement_id: int) -> dict | None:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return None
    res = (
        client.table("evaluations")
        .select("id,achievement_id,position,locked,created_at,updated_at")
        .eq("achievement_id", achievement_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if res is None:
        return None
    return res.data


def list_user_scale(request: Request) -> list[dict]:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return []
    res = (
        client.table("evaluations")
        .select(EVALUATION_SELECT)
        .eq("user_id", user.id)
        .order("position")
        .execute()
    )
    return res.data or []


def _has_approved_completion(request: Request, achievement_id: int) -> bool:
    completion = completions_service.get_user_completion(request, achievement_id)
    return completion is not None and completion.get("status") == "approved"


def can_evaluate(request: Request, achievement: dict) -> tuple[bool, str | None]:
    if request.state.user is None:
        return False, "auth"
    if achievement.get("status") != "published":
        return False, "Это достижение ещё не опубликовано."
    if not _has_approved_completion(request, achievement["id"]):
        return False, "Оценивать можно только подтверждённо выполненные достижения."
    my_eval = get_user_evaluation(request, achievement["id"])
    if my_eval is not None and my_eval.get("locked"):
        return False, "Оценка зафиксирована — достижение уже ранжировано."
    return True, None


def submit(request: Request, achievement_id: int, harder_count: int) -> dict:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise AuthRequiredError("Требуется вход в систему.")
    try:
        res = client.rpc(
            "submit_evaluation",
            {
                "p_achievement_id": achievement_id,
                "p_harder_count": harder_count,
            },
        ).execute()
    except EVALUATION_ERRORS as exc:
        message = str(exc)
        if "LOCKED" in message:
            raise LockedError(
                "Оценка зафиксирована — достижение уже ранжировано."
            ) from exc
        if "NOT_APPROVED" in message:
            raise NotApprovedError(
                "Оценивать можно только подтверждённо выполненные достижения."
            ) from exc
        raise EvaluationError(
            message[:300] or "Не удалось сохранить оценку. Попробуйте ещё раз."
        ) from exc
    if not res.data:
        raise EvaluationError("Не удалось сохранить оценку. Попробуйте ещё раз.")
    return res.data[0]


def evaluate_context(
    request: Request, achievement: dict
) -> tuple[bool, str | None, dict | None]:
    my_eval = get_user_evaluation(request, achievement["id"])
    can, reason = can_evaluate(request, achievement)
    return can, reason, my_eval
