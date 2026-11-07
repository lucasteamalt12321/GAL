from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import achievements as achievements_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service

router = APIRouter(tags=["completions"])

LOGIN_URL = "/auth/login"


@router.post(
    "/achievements/{achievement_id}/complete",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def complete(
    request: Request,
    achievement_id: int,
    description: str = Form(""),
    external_url: str = Form(""),
    harder_count: str = Form(""),
    file: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)

    achievement = achievements_service.get_achievement(request, achievement_id)
    if achievement is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    is_creator = str(request.state.user.id) == str(achievement["creator_id"])
    ranked = achievement.get("ranking_status") == "ranked"
    allowed = not ranked and (
        achievement["status"] == "published"
        or (achievement["status"] == "pending" and is_creator)
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="Achievement not found")

    scale = evaluation_service.list_user_scale(request)
    others = [row for row in scale if row["achievement_id"] != achievement_id]
    parsed = evaluation_service.parse_harder_count(harder_count, len(others))
    if parsed is None:
        return RedirectResponse(
            f"/achievements/{achievement_id}"
            f"?error={quote('Выберите, сложнее или легче ваших достижений новое.')}",
            status_code=303,
        )

    try:
        completions_service.submit_completion(
            request,
            achievement_id=achievement_id,
            description=description,
            external_url=external_url,
            uploaded=file,
            harder_count=parsed,
        )
    except completions_service.CompletionError as exc:
        return RedirectResponse(
            f"/achievements/{achievement_id}?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(
        f"/achievements/{achievement_id}?info=submitted", status_code=303
    )
