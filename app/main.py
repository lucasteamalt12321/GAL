import logging
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from postgrest.exceptions import APIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.csrf import csrf_protect
from app.middleware import UserContextMiddleware
from app.routers import (
    achievements,
    auth,
    completions,
    evaluations,
    health,
    moderation,
    rankings,
    reports,
    users,
)
from app.routers import (
    settings as settings_router,
)
from app.services import auth as auth_service
from app.services import rankings as rankings_service
from app.templating import templates

BASE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("gal.upstream")

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=(
        "Global Achievement List — глобальная платформа рейтинга "
        "человеческих достижений."
    ),
    version=settings.app_version,
    dependencies=[Depends(csrf_protect)],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_middleware(UserContextMiddleware)

# Пути, которые обязаны отвечать JSON (API/документация), а не HTML-страницей.
JSON_PATHS = ("/auth/me", "/health", "/openapi.json", "/docs", "/redoc")
ERROR_TEMPLATES = {
    403: "errors/403.html",
    404: "errors/404.html",
}
DEFAULT_ERROR_TEMPLATE = "errors/error.html"


def _wants_json(request: Request) -> bool:
    path = request.url.path
    if path in JSON_PATHS or path.startswith(("/docs", "/redoc")):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def _upstream_unavailable(request: Request, exc: Exception) -> HTMLResponse:
    """Сбой Supabase/PostgREST не должен превращаться в голый 500.

    Отдаём понятную страницу с кодом 503 (сервис временно недоступен) и
    пишем причину в лог, чтобы сбой был виден, но пользователь не получал
    «Internal Server Error».
    """
    logger.warning("Supabase upstream failure: %s", exc)
    return templates.TemplateResponse(
        request=request,
        name="errors/upstream.html",
        context={},
        status_code=503,
    )


@app.exception_handler(StarletteHTTPException)
async def _handle_http_error(
    request: Request, exc: StarletteHTTPException
) -> HTMLResponse | JSONResponse:
    if _wants_json(request):
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )
    template = ERROR_TEMPLATES.get(exc.status_code, DEFAULT_ERROR_TEMPLATE)
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.exception_handler(httpx.HTTPError)
async def _handle_upstream_transport_error(
    request: Request, exc: httpx.HTTPError
) -> HTMLResponse:
    return _upstream_unavailable(request, exc)


@app.exception_handler(APIError)
async def _handle_upstream_postgrest_error(
    request: Request, exc: APIError
) -> HTMLResponse:
    return _upstream_unavailable(request, exc)


@app.exception_handler(auth_service.SupabaseNotConfiguredError)
async def _handle_supabase_not_configured(
    request: Request, exc: auth_service.SupabaseNotConfiguredError
) -> HTMLResponse:
    return _upstream_unavailable(request, exc)


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(achievements.router)
app.include_router(completions.router)
app.include_router(evaluations.router)
app.include_router(moderation.router)
app.include_router(rankings.router)
app.include_router(reports.router)
app.include_router(settings_router.router)
app.include_router(users.router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    """Лендинг. Данные БД необязательны: при сбое Supabase страница живёт."""
    top_achievements: list[dict] = []
    top_players: list[dict] = []
    try:
        top_achievements = rankings_service.leaderboard_achievements(limit=5)[0]
        top_players = rankings_service.player_leaderboard(limit=5)[0]
    except Exception:  # лендинг не должен падать из-за БД
        logger.warning("Landing page data unavailable", exc_info=True)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "settings": settings,
            "top_achievements": top_achievements,
            "top_players": top_players,
        },
    )
