from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.services import achievements as achievements_service
from app.services import completions as completions_service
from app.services import rankings as rankings_service
from app.services.ranking import player_score as achievement_score
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
    completed = completions_service.list_user_completed_achievements(
        request, profile_row["id"]
    )
    # Временное место топ-5 не даёт постоянных очков: считаем только
    # опубликованные ranked-достижения с permanent_rank и BT-рейтингом.
    # Чисто временные строки имеют permanent_rank=None и отсеиваются сами;
    # ranked-достижение с временным слоем сохраняет permanent_rank и должно
    # давать очки. До миграции 027 RPC не возвращает новые поля — тогда rank
    # и ranking_status отсутствуют, поэтому используем прежний fallback.
    ranked = [
        row
        for row in completed
        if row.get("permanent_rank", row.get("rank"))
        and row.get("ranking_status", "ranked") == "ranked"
        and row.get("bt_rating") is not None
    ]
    max_bt_rating = rankings_service.max_ranked_bt_rating()
    player_score = round(
        sum(achievement_score(row.get("bt_rating"), max_bt_rating) for row in ranked),
        2,
    )
    return templates.TemplateResponse(
        request=request,
        name="users/profile.html",
        context={
            "profile": profile_row,
            "achievements": items,
            "completed": completed,
            "completed_count": len(completed),
            "player_score": player_score,
            "is_owner": is_owner,
            "info": PROFILE_INFO_MESSAGES.get(info, info),
        },
    )
