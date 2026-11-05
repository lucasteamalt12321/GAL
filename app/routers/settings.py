from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import profiles as profiles_service
from app.templating import templates

router = APIRouter(prefix="/settings", tags=["settings"])

LOGIN_URL = "/auth/login"


@router.get("/profile", response_class=HTMLResponse, include_in_schema=False)
def profile_settings(request: Request, info: str | None = None) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    return _render(request, info=info)


@router.post("/profile", response_class=HTMLResponse, include_in_schema=False)
def update_profile(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    avatar_url: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    try:
        profiles_service.update_profile(
            request,
            username=username,
            display_name=display_name,
            avatar_url=avatar_url,
        )
    except profiles_service.ProfileError as exc:
        return _render(
            request,
            error=str(exc),
            username=username,
            display_name=display_name,
            avatar_url=avatar_url,
        )
    return RedirectResponse(f"/users/{username}?info=profile-updated", status_code=303)


@router.post("/password", response_class=HTMLResponse, include_in_schema=False)
def update_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    if new_password != new_password_confirm:
        return _render(request, password_error="Пароли не совпадают.")
    try:
        profiles_service.change_password(
            request,
            current_password=current_password,
            new_password=new_password,
        )
    except profiles_service.ProfileError as exc:
        return _render(request, password_error=str(exc))
    return RedirectResponse("/settings/profile?info=password-updated", status_code=303)


def _render(
    request: Request,
    *,
    error: str | None = None,
    info: str | None = None,
    password_error: str | None = None,
    username: str | None = None,
    display_name: str | None = None,
    avatar_url: str | None = None,
) -> HTMLResponse:
    user = request.state.user
    info_messages = {
        "password-updated": "Пароль изменён.",
        "profile-updated": "Профиль обновлён.",
    }
    return templates.TemplateResponse(
        request=request,
        name="settings/profile.html",
        context={
            "error": error,
            "info": info_messages.get(info, info),
            "password_error": password_error,
            "username": username if username is not None else (user.username or ""),
            "display_name": display_name
            if display_name is not None
            else (user.display_name or ""),
            "avatar_url": avatar_url
            if avatar_url is not None
            else (user.avatar_url or ""),
        },
    )
