import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import get_settings
from app.dependencies import get_current_user
from app.forms import body_field
from app.services import auth as auth_service
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

AUTH_REDIRECT = "/"


def _error_message(exc: Exception) -> str:
    raw = getattr(exc, "message", None) or getattr(exc, "msg", None)
    if not raw:
        raw = str(exc)
    if not raw or len(raw) > 300:
        raw = "Не удалось выполнить запрос. Проверьте данные и попробуйте ещё раз."
    return raw


def _page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "error": ctx.pop("error", None),
            "info": ctx.pop("info", None),
            **ctx,
        },
    )


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request) -> HTMLResponse:
    if request.state.user:
        return RedirectResponse(AUTH_REDIRECT, status_code=303)
    return _page(request, "auth/login.html", email="")


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login(request: Request) -> HTMLResponse:
    email = (await body_field(request, "email")).strip()
    password = await body_field(request, "password")
    try:
        client = auth_service.new_anon_client()
        res = client.auth.sign_in_with_password({"email": email, "password": password})
    except auth_service.AUTH_ERRORS as exc:
        return _page(request, "auth/login.html", error=_error_message(exc), email=email)
    session = res.session
    if session is None:
        return _page(
            request, "auth/login.html", error="Не удалось получить сессию.", email=email
        )
    response = RedirectResponse(AUTH_REDIRECT, status_code=303)
    auth_service.set_auth_cookies(response, session)
    return response


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
def register_page(request: Request) -> HTMLResponse:
    if request.state.user:
        return RedirectResponse(AUTH_REDIRECT, status_code=303)
    return _page(request, "auth/register.html", username="", display_name="", email="")


@router.post("/register", response_class=HTMLResponse, include_in_schema=False)
async def register(request: Request) -> HTMLResponse:
    username = (await body_field(request, "username")).strip()
    display_name = (await body_field(request, "display_name")).strip()
    email = (await body_field(request, "email")).strip()
    password = await body_field(request, "password")
    username_error = auth_service.validate_username(username)
    if username_error:
        return _page(
            request,
            "auth/register.html",
            error=username_error,
            username=username,
            display_name=display_name,
            email=email,
        )
    try:
        if not auth_service.username_available(username):
            return _page(
                request,
                "auth/register.html",
                error="Это имя пользователя уже занято. Выберите другое.",
                username="",
                display_name=display_name,
                email=email,
            )
        client = auth_service.new_anon_client()
        res = client.auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {
                    "data": {"username": username, "display_name": display_name}
                },
            }
        )
    except auth_service.AUTH_ERRORS as exc:
        return _page(
            request,
            "auth/register.html",
            error=_error_message(exc),
            username=username,
            display_name=display_name,
            email=email,
        )
    session = res.session
    if session is not None:
        response = RedirectResponse(AUTH_REDIRECT, status_code=303)
        auth_service.set_auth_cookies(response, session)
        return response
    return _page(
        request,
        "auth/register.html",
        info="Регистрация завершена. Подтвердите email, перейдя по ссылке из письма.",
        username=username,
        display_name=display_name,
        email=email,
    )


@router.get("/recover", response_class=HTMLResponse, include_in_schema=False)
def recover_page(request: Request) -> HTMLResponse:
    return _page(request, "auth/recover.html", email="")


@router.post("/recover", response_class=HTMLResponse, include_in_schema=False)
async def recover(request: Request) -> HTMLResponse:
    email = (await body_field(request, "email")).strip()
    options = {}
    app_url = get_settings().app_url.strip()
    if app_url:
        options["redirect_to"] = f"{app_url.rstrip('/')}/auth/login"
    try:
        client = auth_service.new_anon_client()
        client.auth.reset_password_for_email(email, options=options)
    except auth_service.AUTH_ERRORS as exc:
        return _page(
            request, "auth/recover.html", error=_error_message(exc), email=email
        )
    return _page(
        request,
        "auth/recover.html",
        info="Если такой email зарегистрирован, мы отправили ссылку для сброса пароля.",
        email="",
    )


@router.post("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    access = request.cookies.get(auth_service.SESSION_COOKIE)
    refresh = request.cookies.get(auth_service.REFRESH_COOKIE)
    if access and refresh:
        try:
            auth_service.new_user_client(access, refresh).auth.sign_out()
        except auth_service.AUTH_ERRORS:
            logger.debug("Supabase sign_out failed during logout", exc_info=True)
    response = RedirectResponse(AUTH_REDIRECT, status_code=303)
    auth_service.clear_auth_cookies(response)
    return response


@router.get("/me", response_class=JSONResponse)
def me(
    user: Annotated[auth_service.CurrentUser, Depends(get_current_user)],
) -> JSONResponse:
    return JSONResponse(
        content={
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "avatar_url": user.avatar_url,
        }
    )
