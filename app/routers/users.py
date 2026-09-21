from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.services import achievements as achievements_service
from app.templating import templates

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{username}", response_class=HTMLResponse, include_in_schema=False)
def profile(request: Request, username: str) -> HTMLResponse:
    profile_row = achievements_service.get_profile(request, username)
    if profile_row is None:
        raise HTTPException(status_code=404, detail="User not found")
    items = achievements_service.list_achievements_by_creator(
        request, profile_row["id"]
    )
    user = request.state.user
    is_owner = user is not None and str(user.id) == str(profile_row["id"])
    return templates.TemplateResponse(
        request=request,
        name="users/profile.html",
        context={
            "profile": profile_row,
            "achievements": items,
            "is_owner": is_owner,
        },
    )
