"""Защита форм от CSRF (double-submit cookie).

Идея: при первом посещении браузеру выдаётся httpOnly-кука `gal_csrf`
(random token). Каждая форма несёт тот же токен в скрытом поле `_csrf`.
При POST-запросе, когда кука присутствует в запросе, middleware сверяет
значение из тела/заголовка с кукой константным сравнением.

SameSite=Lax уже блокирует отправку куки в кросс-сайтовых POST, поэтому
проверка включается только при наличии куки (реальные браузеры её всегда
получают на первой навигации).
"""

from __future__ import annotations

import secrets

from fastapi import Request

from app.config import get_settings
from app.forms import csrf_from_body

CSRF_COOKIE = "gal_csrf"
HEADER = "X-CSRF-Token"
MAX_AGE = 60 * 60 * 24 * 30


def new_token() -> str:
    return secrets.token_urlsafe(32)


def cookie_kwargs() -> dict:
    settings = get_settings()
    return {
        "max_age": MAX_AGE,
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": settings.app_env == "production",
    }


async def _posted_token(request: Request) -> str | None:
    header = request.headers.get(HEADER)
    if header:
        return header
    return await csrf_from_body(request)


async def check(request: Request) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie:
        return True
    posted = await _posted_token(request)
    if not posted:
        return False
    return secrets.compare_digest(posted, cookie)
