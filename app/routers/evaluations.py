from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import achievements as achievements_service
from app.services import evaluation as evaluation_service
from app.templating import templates

router = APIRouter(tags=["evaluations"])

LOGIN_URL = "/auth/login"


def _achievement_or_404(request: Request, achievement_id: int) -> dict:
    item = achievements_service.get_achievement(request, achievement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    return item


@router.get(
    "/achievements/{achievement_id}/evaluate",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def evaluate_page(
    request: Request, achievement_id: int, error: str | None = None
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    item = _achievement_or_404(request, achievement_id)

    can, reason, my_eval = evaluation_service.evaluate_context(request, item)
    if not can:
        return RedirectResponse(
            f"/achievements/{achievement_id}?error={quote(reason or 'Оценка недоступна')}",
            status_code=303,
        )

    scale = evaluation_service.list_user_scale(request)
    others = [e for e in scale if e["achievement_id"] != achievement_id]
    return templates.TemplateResponse(
        request=request,
        name="evaluations/evaluate.html",
        context={
            "achievement": item,
            "scale": others,
            "my_evaluation": my_eval,
            "error": error,
        },
    )


@router.post(
    "/achievements/{achievement_id}/evaluate",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def evaluate(
    request: Request,
    achievement_id: int,
    harder_count: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    _achievement_or_404(request, achievement_id)

    if not harder_count.isdigit():
        return RedirectResponse(
            f"/achievements/{achievement_id}/evaluate?error={quote('Выберите позицию.')}",
            status_code=303,
        )

    scale = evaluation_service.list_user_scale(request)
    others = [e for e in scale if e["achievement_id"] != achievement_id]
    count = int(harder_count)
    if count < 0 or count > len(others):
        return RedirectResponse(
            f"/achievements/{achievement_id}/evaluate?error={quote('Недопустимая позиция.')}",
            status_code=303,
        )

    try:
        evaluation_service.submit(request, achievement_id, count)
    except evaluation_service.EvaluationError as exc:
        return RedirectResponse(
            f"/achievements/{achievement_id}?error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        f"/achievements/{achievement_id}?info=evaluated", status_code=303
    )
