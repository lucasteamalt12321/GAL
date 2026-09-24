from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.middleware import UserContextMiddleware
from app.routers import achievements, auth, completions, health, moderation, users
from app.templating import templates

BASE_DIR = Path(__file__).resolve().parent

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=(
        "Global Achievement List — глобальная платформа рейтинга "
        "человеческих достижений."
    ),
    version=settings.app_version,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_middleware(UserContextMiddleware)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(achievements.router)
app.include_router(completions.router)
app.include_router(moderation.router)
app.include_router(users.router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"settings": settings},
    )
