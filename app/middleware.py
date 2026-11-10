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


def _add_security_headers(response) -> None:
    """Базовые защитные заголовки для всех ответов.

    CSP ограничен директивами, не ломающими inline-скрипты (nonce для них
    потребовал бы переделки шаблонов), но закрывает framing и подмену базы.
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
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
        _add_security_headers(response)
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
