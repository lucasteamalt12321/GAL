from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import achievements as achievements_service
from app.services import reports as reports_service
from app.templating import templates

router = APIRouter(tags=["reports"])

LOGIN_URL = "/auth/login"
QUEUE_URL = "/reports"
MODERATOR_ROLES = ("moderator", "admin")


def _moderator_guard(request: Request) -> RedirectResponse | None:
    user = request.state.user
    if user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    if user.role not in MODERATOR_ROLES:
        raise HTTPException(status_code=403, detail="Moderator access required")
    return None


@router.get(
    "/achievements/{achievement_id}/report",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def report_form(
    request: Request, achievement_id: int, error: str | None = None
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    achievement = achievements_service.get_achievement(request, achievement_id)
    if achievement is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    return templates.TemplateResponse(
        request=request,
        name="reports/report.html",
        context={"achievement": achievement, "error": error},
    )


@router.post(
    "/achievements/{achievement_id}/report",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def submit_report(
    request: Request,
    achievement_id: int,
    reason: str = Form(...),
    description: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    try:
        reports_service.create_report(
            request,
            achievement_id=achievement_id,
            reason=reason,
            description=description,
        )
    except reports_service.ReportError as exc:
        return RedirectResponse(
            f"/achievements/{achievement_id}/report?error={exc}", status_code=303
        )
    return RedirectResponse(
        f"/achievements/{achievement_id}?info=report-submitted", status_code=303
    )


@router.get("/reports", response_class=HTMLResponse, include_in_schema=False)
def reports_queue(request: Request, error: str | None = None) -> HTMLResponse:
    redirect = _moderator_guard(request)
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
        request=request,
        name="reports/queue.html",
        context={
            "pending_reports": reports_service.list_pending_reports(request),
            "error": error,
        },
    )


@router.post(
    "/reports/{report_id}/accept",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def accept_report(
    request: Request,
    report_id: int,
    resolution_reason: str = Form(""),
) -> HTMLResponse:
    return _decide(request, report_id, "accepted", resolution_reason)


@router.post(
    "/reports/{report_id}/reject",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def reject_report(
    request: Request,
    report_id: int,
    resolution_reason: str = Form(""),
) -> HTMLResponse:
    return _decide(request, report_id, "rejected", resolution_reason)


def _decide(
    request: Request, report_id: int, decision: str, reason: str
) -> HTMLResponse:
    redirect = _moderator_guard(request)
    if redirect is not None:
        return redirect
    try:
        reports_service.decide_report(request, report_id, decision, reason)
    except reports_service.ReportError as exc:
        return RedirectResponse(f"{QUEUE_URL}?error={exc}", status_code=303)
    return RedirectResponse(QUEUE_URL, status_code=303)
