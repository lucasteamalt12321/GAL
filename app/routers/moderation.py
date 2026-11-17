from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import moderation as moderation_service
from app.services import top5 as top5_service
from app.templating import templates

router = APIRouter(prefix="/moderation", tags=["moderation"])

LOGIN_URL = "/auth/login"
QUEUE_URL = "/moderation"
MODERATOR_ROLES = ("moderator", "admin")


def _guard(request: Request) -> RedirectResponse | None:
    user = request.state.user
    if user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    if user.role not in MODERATOR_ROLES:
        raise HTTPException(status_code=403, detail="Moderator access required")
    return None


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def queue(request: Request, error: str | None = None) -> HTMLResponse:
    redirect = _guard(request)
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
        request=request,
        name="moderation/queue.html",
        context={
            "pending_achievements": moderation_service.list_pending_achievements(
                request
            ),
            "pending_completions": moderation_service.list_pending_completions(request),
            "top5_applications": top5_service.try_moderation_queue(request),
            "error": error,
        },
    )


@router.get("/history", response_class=HTMLResponse, include_in_schema=False)
def history(request: Request) -> HTMLResponse:
    redirect = _guard(request)
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
        request=request,
        name="moderation/history.html",
        context={"reviews": moderation_service.list_history(request)},
    )


@router.post(
    "/achievements/{achievement_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def decide_achievement(
    request: Request,
    achievement_id: int,
    decision: str = Form(...),
    reason: str = Form(""),
) -> HTMLResponse:
    redirect = _guard(request)
    if redirect is not None:
        return redirect
    try:
        moderation_service.decide_achievement(request, achievement_id, decision, reason)
    except moderation_service.ModerationError as exc:
        return RedirectResponse(f"{QUEUE_URL}?error={exc}", status_code=303)
    except moderation_service.MODERATION_ERRORS:
        return RedirectResponse(
            f"{QUEUE_URL}?error=Не удалось сохранить решение. Попробуйте ещё раз.",
            status_code=303,
        )
    return RedirectResponse(QUEUE_URL, status_code=303)


@router.post(
    "/achievements/{achievement_id}/hide",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def hide_achievement(
    request: Request,
    achievement_id: int,
    reason: str = Form(""),
) -> HTMLResponse:
    redirect = _guard(request)
    if redirect is not None:
        return redirect
    try:
        moderation_service.hide_achievement(request, achievement_id, reason)
    except moderation_service.ModerationError as exc:
        return RedirectResponse(
            f"/achievements/{achievement_id}?error={exc}", status_code=303
        )
    except moderation_service.MODERATION_ERRORS:
        return RedirectResponse(
            f"/achievements/{achievement_id}"
            "?error=Не удалось скрыть достижение. Попробуйте ещё раз.",
            status_code=303,
        )
    return RedirectResponse(
        f"/achievements/{achievement_id}?info=hidden", status_code=303
    )


@router.post(
    "/top5/{application_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def decide_top5(
    request: Request,
    application_id: int,
    decision: str = Form(...),
    reason: str = Form(""),
    estimated_position: str = Form(""),
) -> HTMLResponse:
    redirect = _guard(request)
    if redirect is not None:
        return redirect
    try:
        top5_service.decide_application(
            request,
            application_id,
            decision=decision,
            reason=reason,
            estimated_position=estimated_position,
        )
    except top5_service.Top5Error as exc:
        return RedirectResponse(
            f"/top5/{application_id}?error={quote(str(exc))}", status_code=303
        )
    except top5_service.TOP5_ERRORS:
        return RedirectResponse(
            f"/top5/{application_id}"
            f"?error={quote('Не удалось сохранить решение. Попробуйте ещё раз.')}",
            status_code=303,
        )
    return RedirectResponse(f"/top5/{application_id}?info=decided", status_code=303)


@router.post(
    "/completions/{completion_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def decide_completion(
    request: Request,
    completion_id: int,
    decision: str = Form(...),
    reason: str = Form(""),
) -> HTMLResponse:
    redirect = _guard(request)
    if redirect is not None:
        return redirect
    try:
        moderation_service.decide_completion(request, completion_id, decision, reason)
    except moderation_service.ModerationError as exc:
        return RedirectResponse(f"{QUEUE_URL}?error={exc}", status_code=303)
    except moderation_service.MODERATION_ERRORS:
        return RedirectResponse(
            f"{QUEUE_URL}?error=Не удалось сохранить решение. Попробуйте ещё раз.",
            status_code=303,
        )
    return RedirectResponse(QUEUE_URL, status_code=303)
