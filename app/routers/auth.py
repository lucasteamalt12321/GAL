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


def _safe_next(value: str | None) -> str:
    """Only allow same-site absolute paths as a post-login destination."""
    target = (value or "").strip()
    if not target.startswith("/") or target.startswith("//"):
        return AUTH_REDIRECT
    if target.startswith(("/auth/login", "/auth/logout")):
        return AUTH_REDIRECT
    return target


def _error_message(exc: Exception) -> str:
    raw = getattr(exc, "message", None) or getattr(exc, "msg", None) or str(exc)
    logger.warning("Supabase auth error: %s (code=%s)", raw, getattr(exc, "code", None))
    return auth_service.friendly_auth_error(exc)


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
def login_page(
    request: Request, error: str | None = None, next: str | None = None
) -> HTMLResponse:
    target = _safe_next(next)
    if request.state.user:
        return RedirectResponse(target, status_code=303)
    return _page(request, "auth/login.html", email="", error=error, next=target)


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login(request: Request) -> HTMLResponse:
    identifier = (await body_field(request, "email")).strip()
    password = await body_field(request, "password")
    target = _safe_next(await body_field(request, "next"))
    email = auth_service.resolve_login_email(identifier)
    if email is None:
        return _page(
            request,
            "auth/login.html",
            error="Неверный email или пароль.",
            email=identifier,
            next=target,
        )
    try:
        client = auth_service.new_anon_client()
        res = client.auth.sign_in_with_password({"email": email, "password": password})
    except auth_service.AUTH_ERRORS as exc:
        return _page(
            request,
            "auth/login.html",
            error=_error_message(exc),
            email=identifier,
            next=target,
        )
    session = res.session
    if session is None:
        return _page(
            request,
            "auth/login.html",
            error="Не удалось получить сессию.",
            email=identifier,
            next=target,
        )
    response = RedirectResponse(target, status_code=303)
    auth_service.set_auth_cookies(response, session)
    return response


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
def register_page(request: Request, next: str | None = None) -> HTMLResponse:
    target = _safe_next(next)
    if request.state.user:
        return RedirectResponse(target, status_code=303)
    return _page(
        request,
        "auth/register.html",
        username="",
        display_name="",
        email="",
        next=target,
    )


@router.post("/register", response_class=HTMLResponse, include_in_schema=False)
async def register(request: Request) -> HTMLResponse:
    username = (await body_field(request, "username")).strip()
    display_name = (await body_field(request, "display_name")).strip()
    email = (await body_field(request, "email")).strip()
    password = await body_field(request, "password")
    password_confirm = await body_field(request, "password_confirm")
    target = _safe_next(await body_field(request, "next"))
    form = {
        "username": username,
        "display_name": display_name,
        "email": email,
        "next": target,
    }
    if password_confirm and password != password_confirm:
        return _page(
            request, "auth/register.html", error="Пароли не совпадают.", **form
        )
    username_error = auth_service.validate_username(username)
    if username_error:
        return _page(request, "auth/register.html", error=username_error, **form)
    email_error = auth_service.validate_email(email)
    if email_error:
        return _page(request, "auth/register.html", error=email_error, **form)
    try:
        if not auth_service.username_available(username):
            return _page(
                request,
                "auth/register.html",
                error="Это имя пользователя уже занято. Выберите другое.",
                **form,
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
        return _page(request, "auth/register.html", error=_error_message(exc), **form)
    session = res.session
    if session is not None:
        response = RedirectResponse(target, status_code=303)
        auth_service.set_auth_cookies(response, session)
        return response
    return _page(
        request,
        "auth/register.html",
        info="Регистрация завершена. Подтвердите email, перейдя по ссылке из письма.",
        **form,
    )


@router.get("/recover", response_class=HTMLResponse, include_in_schema=False)
def recover_page(request: Request) -> HTMLResponse:
    return _page(request, "auth/recover.html", email="")


@router.post("/recover", response_class=HTMLResponse, include_in_schema=False)
async def recover(request: Request) -> HTMLResponse:
    email = (await body_field(request, "email")).strip()
    options = {}
    app_url = get_settings().app_url.strip() or str(request.base_url).rstrip("/")
    if app_url:
        options["redirect_to"] = f"{app_url.rstrip('/')}/auth/update-password"
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


@router.get("/update-password", response_class=HTMLResponse, include_in_schema=False)
def update_password_page(request: Request) -> HTMLResponse:
    """Страница завершения сброса пароля.

    GoTrue присылает токены восстановления во фрагменте URL (#access_token=...),
    который не попадает на сервер. Поэтому новый пароль отправляется из браузера
    напрямую в Supabase Auth, а сервер только отдаёт публичные URL/anon key.
    """
    settings = get_settings()
    return _page(
        request,
        "auth/update_password.html",
        supabase_url=settings.supabase_url,
        supabase_anon_key=settings.supabase_anon_key,
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
