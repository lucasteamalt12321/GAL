from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import get_settings
from app.csrf import (
    CSRF_COOKIE,
)
from app.csrf import (
    cookie_kwargs as csrf_cookie_kwargs,
)
from app.csrf import (
    new_token as new_csrf_token,
)
from app.services import auth as auth_service


def _form_action(path: str) -> str:
    """`form-action` для CSP: 'self' плюс origin'ы OAuth-клиентов на authorize.

    Форма согласия OAuth POST'ится на /oauth/authorize и получает 303 на
    cross-origin redirect_uri клиента. CSP form-action проверяет всю цепочку
    редиректов формы, поэтому 'self' блокирует вход через GAL. Разрешаем только
    origin'ы зарегистрированных redirect URI (они и так валидируются провайдером)
    и только на маршруте authorize; остальные страницы остаются с 'self'.
    """
    if path != "/oauth/authorize":
        return "'self'"
    from app.services import oauth_provider as provider

    origins: set[str] = set()
    for client in provider._clients().values():
        for uri in client["redirect_uris"]:
            parts = urlsplit(uri)
            if parts.scheme in ("http", "https") and parts.netloc:
                origins.add(f"{parts.scheme}://{parts.netloc}")
    if not origins:
        return "'self'"
    return "'self' " + " ".join(sorted(origins))


def _add_security_headers(response, path: str) -> None:
    """Базовые защитные заголовки для всех ответов.

    CSP ограничен директивами, не ломающими inline-скрипты (nonce для них
    потребовал бы переделки шаблонов), но закрывает framing и подмену базы.
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        f"frame-ancestors 'none'; base-uri 'self'; form-action {_form_action(path)}",
    )
    if get_settings().is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )


def _should_skip(path: str) -> bool:
    return path.startswith("/static") or path == "/health"


class UserContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        request.state.pending_cookies = []
        request.state.csrf_token = new_csrf_token()
        if not _should_skip(request.url.path):
            csrf_cookie = request.cookies.get(CSRF_COOKIE)
            if csrf_cookie:
                request.state.csrf_token = csrf_cookie
            request.state.user = await run_in_threadpool(
                auth_service.resolve_user, request
            )
        response = await call_next(request)
        _add_security_headers(response, request.url.path)
        for name, value, kwargs in getattr(request.state, "pending_cookies", []):
            # kwargs важны для удаления cookie (max_age=0): раньше они
            # игнорировались, и «удалённая» битая сессия жила ещё 30 дней.
            response.set_cookie(
                name,
                value,
                **{**auth_service._cookie_kwargs(), **kwargs},
            )
        if request.cookies.get(CSRF_COOKIE) is None:
            response.set_cookie(
                CSRF_COOKIE, request.state.csrf_token, **csrf_cookie_kwargs()
            )
        return response
