from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import achievements as achievements_service
from app.services import completions as completions_service

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
    file: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)

    achievement = achievements_service.get_achievement(request, achievement_id)
    if achievement is None or achievement["status"] != "published":
        raise HTTPException(status_code=404, detail="Achievement not found")

    try:
        completions_service.submit_completion(
            request,
            achievement_id=achievement_id,
            description=description,
            external_url=external_url,
            uploaded=file,
        )
    except completions_service.CompletionError as exc:
        return RedirectResponse(
            f"/achievements/{achievement_id}?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(
        f"/achievements/{achievement_id}?info=submitted", status_code=303
    )
