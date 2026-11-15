from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import achievements as achievements_service
from app.services import top5 as top5_service
from app.templating import templates

router = APIRouter(prefix="/top5", tags=["top5"])

LOGIN_URL = "/auth/login"
MY_URL = "/top5"

INFO_MESSAGES = {
    "applied": "Заявка отправлена на модерацию.",
    "cancelled": "Заявка отозвана.",
    "evaluated": "Оценка сохранена.",
    "invited": "Эксперт приглашён.",
    "declined": "Приглашение отклонено.",
    "decided": "Решение сохранено.",
}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _error_url(url: str, message: str) -> str:
    return f"{url}?error={quote(message)}"


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def my_page(
    request: Request,
    info: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    applications = top5_service.list_my_applications(request)
    invitations = top5_service.list_expert_invitations(request)
    return templates.TemplateResponse(
        request=request,
        name="top5/index.html",
        context={
            "applications": applications,
            "invitations": invitations,
            "info": INFO_MESSAGES.get(info, info),
            "error": error,
        },
    )


@router.get(
    "/apply/{achievement_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def apply_page(
    request: Request, achievement_id: int, error: str | None = None
) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    achievement = achievements_service.get_achievement(request, achievement_id)
    if achievement is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    context = top5_service.application_context(request, achievement)
    if context["my_application"] is not None:
        application_id = context["my_application"]["id"]
        return _redirect(f"/top5/{application_id}")
    if not context["can_apply"]:
        return _redirect(
            _error_url(
                f"/achievements/{achievement_id}",
                context["apply_blocked_reason"] or "Заявка недоступна.",
            )
        )
    return templates.TemplateResponse(
        request=request,
        name="top5/apply.html",
        context={
            "achievement": achievement,
            "error": error,
        },
    )


@router.post(
    "/apply/{achievement_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def apply(
    request: Request,
    achievement_id: int,
    proposed_position: str = Form(""),
    justification: str = Form(""),
    evidence_url: str = Form(""),
    evidence_note: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    try:
        top5_service.submit_application(
            request,
            achievement_id,
            proposed_position=proposed_position,
            justification=justification,
            evidence_url=evidence_url,
            evidence_note=evidence_note,
        )
    except top5_service.Top5Error as exc:
        return _redirect(_error_url(f"/top5/apply/{achievement_id}", str(exc)))
    except top5_service.TOP5_ERRORS:
        return _redirect(
            _error_url(
                f"/top5/apply/{achievement_id}",
                "Не удалось отправить заявку. Попробуйте ещё раз.",
            )
        )
    return _redirect(f"{MY_URL}?info=applied")


@router.post(
    "/{application_id}/cancel",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def cancel(request: Request, application_id: int) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    try:
        top5_service.cancel_application(request, application_id)
    except top5_service.Top5Error as exc:
        return _redirect(_error_url(MY_URL, str(exc)))
    except top5_service.TOP5_ERRORS:
        return _redirect(
            _error_url(MY_URL, "Не удалось отозвать заявку. Попробуйте ещё раз.")
        )
    return _redirect(f"{MY_URL}?info=cancelled")


@router.get("/{application_id}", response_class=HTMLResponse, include_in_schema=False)
def detail(
    request: Request,
    application_id: int,
    info: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    bundle = top5_service.application_bundle(request, application_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return templates.TemplateResponse(
        request=request,
        name="top5/detail.html",
        context={
            **bundle,
            "info": INFO_MESSAGES.get(info, info),
            "error": error,
        },
    )


@router.post(
    "/{application_id}/evaluate",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def evaluate(
    request: Request,
    application_id: int,
    estimated_position: str = Form(""),
    note: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    try:
        top5_service.submit_evaluation(
            request,
            application_id,
            estimated_position=estimated_position,
            note=note,
        )
    except top5_service.Top5Error as exc:
        return _redirect(_error_url(f"/top5/{application_id}", str(exc)))
    except top5_service.TOP5_ERRORS:
        return _redirect(
            _error_url(
                f"/top5/{application_id}",
                "Не удалось сохранить оценку. Попробуйте ещё раз.",
            )
        )
    return _redirect(f"/top5/{application_id}?info=evaluated")


@router.post(
    "/{application_id}/invite",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def invite(
    request: Request,
    application_id: int,
    username: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    try:
        top5_service.invite_expert(request, application_id, username)
    except top5_service.Top5Error as exc:
        return _redirect(_error_url(f"/top5/{application_id}", str(exc)))
    except top5_service.TOP5_ERRORS:
        return _redirect(
            _error_url(
                f"/top5/{application_id}",
                "Не удалось пригласить эксперта. Попробуйте ещё раз.",
            )
        )
    return _redirect(f"/top5/{application_id}?info=invited")


@router.post(
    "/requests/{request_id}/decline",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def decline(
    request: Request,
    request_id: int,
    note: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    try:
        top5_service.decline_invitation(request, request_id, note)
    except top5_service.Top5Error as exc:
        return _redirect(_error_url(MY_URL, str(exc)))
    except top5_service.TOP5_ERRORS:
        return _redirect(_error_url(MY_URL, "Не удалось отклонить приглашение."))
    return _redirect(f"{MY_URL}?info=declined")
