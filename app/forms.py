"""Чтение параметров POST-запроса: form-urlencoded / multipart или JSON."""

from __future__ import annotations

from fastapi import Request
from starlette.exceptions import HTTPException as StarletteHTTPException

_BODY_ERRORS = (ValueError, RuntimeError, StarletteHTTPException)


def _is_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("content-type") or "").lower()


async def body_data(request: Request) -> dict:
    """Возвращает dict параметров из тела (form или JSON)."""
    if _is_json(request):
        try:
            data = await request.json()
        except _BODY_ERRORS:
            return {}
        return data if isinstance(data, dict) else {}
    try:
        form = await request.form()
    except _BODY_ERRORS:
        return {}
    return {key: value for key, value in form.multi_items()}


async def body_field(request: Request, name: str, default: str = "") -> str:
    value = (await body_data(request)).get(name, default)
    if value is None:
        return default
    return str(value)


async def csrf_from_body(request: Request) -> str | None:
    """CSRF-токен из тела form/JSON (для double-submit проверки)."""
    data = await body_data(request)
    field = data.get("_csrf")
    if field is None:
        return None
    return str(field)