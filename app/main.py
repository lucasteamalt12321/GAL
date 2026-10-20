import logging
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from postgrest.exceptions import APIError

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


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(achievements.router)
app.include_router(completions.router)
app.include_router(evaluations.router)
app.include_router(moderation.router)
app.include_router(rankings.router)
app.include_router(reports.router)
app.include_router(users.router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"settings": settings},
    )
