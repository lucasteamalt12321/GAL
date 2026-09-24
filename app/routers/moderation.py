from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import moderation as moderation_service
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
            "error": error,
        },
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
    return RedirectResponse(QUEUE_URL, status_code=303)


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
    return RedirectResponse(QUEUE_URL, status_code=303)
