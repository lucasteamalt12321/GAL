from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.services import achievements as achievements_service
from app.templating import templates

router = APIRouter(prefix="/users", tags=["users"])

MODERATOR_ROLES = ("moderator", "admin")
MODERATOR_VISIBLE_STATUSES = ("published", "pending", "rejected", "deleted")
OWNER_VISIBLE_STATUSES = ("published", "pending", "rejected")

PROFILE_INFO_MESSAGES = {
    "deleted": "Достижение удалено.",
    "profile-updated": "Профиль обновлён.",
}


@router.get("/{username}", response_class=HTMLResponse, include_in_schema=False)
def profile(request: Request, username: str, info: str | None = None) -> HTMLResponse:
    profile_row = achievements_service.get_profile(request, username)
    if profile_row is None:
        raise HTTPException(status_code=404, detail="User not found")
    user = request.state.user
    is_owner = user is not None and str(user.id) == str(profile_row["id"])
    is_moderator = user is not None and user.role in MODERATOR_ROLES

    if is_moderator:
        statuses = MODERATOR_VISIBLE_STATUSES
    elif is_owner:
        statuses = OWNER_VISIBLE_STATUSES
    else:
        statuses = ("published",)
    items = achievements_service.list_achievements_by_creator(
        request, profile_row["id"], statuses=statuses
    )
    return templates.TemplateResponse(
        request=request,
        name="users/profile.html",
        context={
            "profile": profile_row,
            "achievements": items,
            "is_owner": is_owner,
            "info": PROFILE_INFO_MESSAGES.get(info, info),
        },
    )
