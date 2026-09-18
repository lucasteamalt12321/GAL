from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.routers import health

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

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(health.router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"settings": settings},
    )
