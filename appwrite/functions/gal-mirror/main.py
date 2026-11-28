"""Appwrite Function-зеркало GAL.

адаптер принимает HTTP-запрос из рантайма Appwrite и вызывает тот же
FastAPI-объект `app.main:app`, который работает на Vercel. База данных,
авторизация и Storage остаются в прежнем Supabase — функция только
перенаправляет запросы в процесс приложения.

Entrypoint: main.py -> main(context)
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx

# Внешний прокси aw2 обрывает ответы после ~5 с, раньше Supabase-таймаута 6 с.
os.environ.setdefault("SUPABASE_TIMEOUT_SECONDS", "2.0")

# open-runtimes загружает точку входа как пакет `function.main`, поэтому
# каталог функции не становится корнем импорта: `from app.main ...` падает
# с `ModuleNotFoundError: app`. Добавляем каталог адаптера в sys.path явно.
_FUNCTION_DIR = os.path.dirname(os.path.abspath(__file__))
if _FUNCTION_DIR not in sys.path:
    sys.path.insert(0, _FUNCTION_DIR)

from app.main import app as asgi_app

_TRANSPORT = httpx.ASGITransport(app=asgi_app, raise_app_exceptions=False)
_BASE_URL = os.environ.get("MIRROR_BASE_URL", "http://appwrite.local")

# Заголовки, которые нельзя передавать внутреннему ASGI-приложению:
# служебные заголовки Appwrite/open-runtimes и hop-by-hop поля.
_DROP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


def _request_headers(context: Any) -> dict[str, str]:
    raw = getattr(context.req, "headers", None) or {}
    headers: dict[str, str] = {}
    for key, value in raw.items():
        lower = str(key).lower()
        if lower in _DROP_HEADERS:
            continue
        if lower.startswith(("x-open-runtimes-", "x-appwrite-")):
            continue
        headers[lower] = str(value)
    return headers


def _target(context: Any) -> str:
    path = getattr(context.req, "path", None) or "/"
    if not path.startswith("/"):
        path = "/" + path
    query = getattr(context.req, "query_string", None) or ""
    return path + ("?" + query if query else "")


def _body(context: Any) -> bytes:
    binary = getattr(context.req, "body_binary", None)
    if isinstance(binary, (bytes, bytearray)):
        return bytes(binary)
    text = getattr(context.req, "body_text", None)
    if text is None:
        text = getattr(context.req, "body_raw", None)
    if text is None:
        text = getattr(context.req, "body", None)
    if isinstance(text, (dict, list)):
        import json

        return json.dumps(text).encode("utf-8")
    return str(text or "").encode("utf-8")


def _response_headers(response: httpx.Response) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in response.headers.items():
        lower = key.lower()
        if lower in {"content-length", "transfer-encoding", "connection"}:
            continue
        if lower == "set-cookie":
            # Рантайм open-runtimes v5 поддерживает список значений — это
            # единственный способ сохранить несколько Set-Cookie (сессия,
            # refresh, CSRF) без потери.
            result[lower] = list(response.headers.get_list("set-cookie"))
            continue
        result[lower] = value
    return result


async def main(context: Any) -> Any:
    """Точка входа Appwrite Function (асинхронный рантайм v5)."""
    method = (getattr(context.req, "method", "GET") or "GET").upper()
    target = _target(context)
    context.log(f"mirror {method} {target}")

    try:
        async with httpx.AsyncClient(
            transport=_TRANSPORT,
            base_url=_BASE_URL,
            follow_redirects=False,
            timeout=httpx.Timeout(60.0),
        ) as client:
            response = await client.request(
                method,
                target,
                headers=_request_headers(context),
                content=_body(context),
            )
    except Exception as exc:  # noqa: BLE001 — граница рантайма, логируем всё
        context.error(f"mirror dispatch failed: {exc!r}")
        return context.res.text(
            "Mirror upstream error",
            500,
            {"content-type": "text/plain"},
        )

    return context.res.binary(
        bytes(response.content),
        response.status_code,
        _response_headers(response),
    )
