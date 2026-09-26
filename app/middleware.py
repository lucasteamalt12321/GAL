from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.config import get_settings
from app.csrf import (
    CSRF_COOKIE,
)
from app.csrf import (
    check as csrf_check,
)
from app.csrf import (
    cookie_kwargs as csrf_cookie_kwargs,
)
from app.csrf import (
    new_token as new_csrf_token,
)
from app.services import auth as auth_service


def _should_skip(path: str) -> bool:
    return path.startswith("/static") or path == "/health"


class UserContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        request.state.pending_cookies = []
        request.state.csrf_token = new_csrf_token()
        if not _should_skip(request.url.path):
            enforce_csrf = get_settings().app_env == "production"
            if (
                request.method == "POST"
                and enforce_csrf
                and not await csrf_check(request)
            ):
                return PlainTextResponse("CSRF token mismatch.", status_code=403)
            csrf_cookie = request.cookies.get(CSRF_COOKIE)
            if csrf_cookie:
                request.state.csrf_token = csrf_cookie
            request.state.user = await run_in_threadpool(
                auth_service.resolve_user, request
            )
        response = await call_next(request)
        for name, value, kwargs in getattr(request.state, "pending_cookies", []):
            response.set_cookie(name, value, **auth_service._cookie_kwargs())
        if request.cookies.get(CSRF_COOKIE) is None:
            response.set_cookie(
                CSRF_COOKIE, request.state.csrf_token, **csrf_cookie_kwargs()
            )
        return response
