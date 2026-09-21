from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.services import auth as auth_service


def _should_skip(path: str) -> bool:
    return path.startswith("/static") or path == "/health"


class UserContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        request.state.pending_cookies = []
        if not _should_skip(request.url.path):
            request.state.user = await run_in_threadpool(
                auth_service.resolve_user, request
            )
        response = await call_next(request)
        for name, value, kwargs in getattr(request.state, "pending_cookies", []):
            response.set_cookie(name, value, **auth_service._cookie_kwargs())
        return response
