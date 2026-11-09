from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import completions as completions_service
from app.templating import templates

router = APIRouter(prefix="/completions", tags=["completions"])

LOGIN_URL = "/auth/login"


@router.get("/{completion_id}", response_class=HTMLResponse, include_in_schema=False)
def completion_detail(request: Request, completion_id: int) -> HTMLResponse:
    """Карточка прохождения достижения."""
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)

    completion = completions_service.get_user_completion_by_id(request, completion_id)
    if completion is None:
        raise HTTPException(status_code=404, detail="Completion not found")

    # Проверяем доступ: владелец или модератор
    user = request.state.user
    is_owner = str(user.id) == str(completion.get("user_id"))
    is_moderator = user.role in ("moderator", "admin")

    if not is_owner and not is_moderator:
        raise HTTPException(status_code=403, detail="Access denied")

    # Получаем доказательства
    proofs = completions_service.list_proofs(request, completion_id)
    proof_views = completions_service.decorate_proofs(request, proofs)

    # Получаем достижение
    achievement = completions_service.get_achievement_for_completion(
        request, completion_id
    )

    return templates.TemplateResponse(
        request=request,
        name="completions/detail.html",
        context={
            "completion": completion,
            "achievement": achievement,
            "proofs": proof_views,
            "is_owner": is_owner,
            "is_moderator": is_moderator,
        },
    )
