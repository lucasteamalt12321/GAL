from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from postgrest.exceptions import APIError

from app.services import completions as completions_service
from app.services import top5 as top5_service
from app.templating import templates

router = APIRouter(prefix="/submissions", tags=["submissions"])

LOGIN_URL = "/auth/login"
ALLOWED_STATUSES = ("", "pending", "approved", "rejected")
STATUS_LABELS = {
    "pending": "На проверке",
    "approved": "Подтверждено",
    "rejected": "Отклонено",
}
INFO_MESSAGES = {"cancelled": "Заявка в топ-5 отозвана."}


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def my_submissions(
    request: Request,
    status: str = "",
    info: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    status = status if status in ALLOWED_STATUSES else ""
    rows = completions_service.list_user_completions(request)
    if status:
        rows = [row for row in rows if row.get("status") == status]
    counts = {
        "all": len(rows),
        "pending": sum(1 for row in rows if row.get("status") == "pending"),
        "approved": sum(1 for row in rows if row.get("status") == "approved"),
        "rejected": sum(1 for row in rows if row.get("status") == "rejected"),
    }
    try:
        top5_applications = top5_service.list_my_applications(request)
    except (top5_service.Top5Error, APIError):
        top5_applications = []
    return templates.TemplateResponse(
        request=request,
        name="submissions/list.html",
        context={
            "submissions": rows,
            "status": status,
            "counts": counts,
            "status_labels": STATUS_LABELS,
            "top5_applications": top5_applications,
            "info": INFO_MESSAGES.get(info, info),
            "error": error,
        },
    )
