"""Вход в GAL через LTHub (OAuth 2.0 authorization code + PKCE).

GAL — публичный клиент: секрет не хранится, защита строится на PKCE (S256)
и одноразовом `state` в HttpOnly-cookie. LTHub отдаёт opaque access token,
который сразу обменивается на профиль через `/oauth/userinfo`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.services import auth as auth_service
from app.services import lthub_accounts

router = APIRouter(prefix="/auth/lthub", tags=["auth"])

STATE_COOKIE = "gal_lthub_state"
VERIFIER_COOKIE = "gal_lthub_verifier"
NEXT_COOKIE = "gal_lthub_next"
COOKIE_PATH = "/auth/lthub"
COOKIE_MAX_AGE = 600
CALLBACK_PATH = "/auth/lthub/callback"


def _safe_next(value: str | None) -> str:
    target = (value or "").strip()
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    if target.startswith(
        ("/auth/login", "/auth/logout", "/auth/register", "/auth/lthub")
    ):
        return "/"
    return target


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _redirect_uri(request: Request) -> str:
    settings = get_settings()
    if settings.lthub_redirect_uri.strip():
        return settings.lthub_redirect_uri.strip()
    base = settings.app_url.strip() or str(request.base_url).rstrip("/")
    return f"{base.rstrip('/')}{CALLBACK_PATH}"


def _cookie_kwargs() -> dict:
    settings = get_settings()
    return {
        "max_age": COOKIE_MAX_AGE,
        "path": COOKIE_PATH,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.is_production,
    }


def _fail(message: str) -> RedirectResponse:
    response = RedirectResponse(f"/auth/login?error={quote(message)}", status_code=303)
    _clear_cookies(response)
    return response


def _clear_cookies(response: RedirectResponse) -> None:
    response.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
    response.delete_cookie(VERIFIER_COOKIE, path=COOKIE_PATH)
    response.delete_cookie(NEXT_COOKIE, path=COOKIE_PATH)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def start(
    request: Request, next: str | None = None, intent: str | None = None
) -> RedirectResponse:
    settings = get_settings()
    if not settings.lthub_oauth_enabled:
        return _fail("Вход через LTHub отключён.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    params = {
        "response_type": "code",
        "client_id": settings.lthub_client_id,
        "redirect_uri": _redirect_uri(request),
        "state": state,
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if (intent or "").strip().lower() == "register":
        params["intent"] = "register"
    response = RedirectResponse(
        f"{settings.lthub_authorize_url}?{urlencode(params)}", status_code=303
    )
    kwargs = _cookie_kwargs()
    response.set_cookie(STATE_COOKIE, state, **kwargs)
    response.set_cookie(VERIFIER_COOKIE, verifier, **kwargs)
    response.set_cookie(NEXT_COOKIE, _safe_next(next), **kwargs)
    return response


@router.get("/callback", response_class=HTMLResponse, include_in_schema=False)
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    settings = get_settings()
    if not settings.lthub_oauth_enabled:
        return _fail("Вход через LTHub отключён.")
    if error:
        return _fail(f"LTHub отклонил вход: {error}")
    if not code or not state:
        return _fail("LTHub не вернул код авторизации.")

    expected_state = request.cookies.get(STATE_COOKIE)
    verifier = request.cookies.get(VERIFIER_COOKIE)
    if not expected_state or not hmac.compare_digest(state, expected_state):
        return _fail("Неверный state OAuth. Попробуйте войти ещё раз.")
    if not verifier:
        return _fail("Сессия входа через LTHub истекла. Попробуйте ещё раз.")

    try:
        token_response = httpx.post(
            settings.lthub_token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.lthub_client_id,
                "redirect_uri": _redirect_uri(request),
                "code": code,
                "code_verifier": verifier,
            },
            timeout=10,
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        access_token = token_payload.get("access_token")
        if not access_token:
            return _fail("LTHub не выдал access token.")

        user_response = httpx.get(
            settings.lthub_userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_response.raise_for_status()
        user = user_response.json()
    except (httpx.HTTPError, ValueError):
        return _fail("Не удалось получить профиль LTHub. Попробуйте ещё раз.")

    subject = str(user.get("sub") or "").strip()
    login = str(user.get("login") or "").strip()
    display_name = str(user.get("display_name") or user.get("name") or login).strip()
    try:
        session = lthub_accounts.login_with_lthub(
            request,
            subject=subject,
            login=login,
            display_name=display_name,
        )
    except lthub_accounts.LthubAccountError as exc:
        return _fail(str(exc))
    except Exception:  # noqa: BLE001 - не раскрываем внутренние детали
        return _fail("Не удалось войти через LTHub. Попробуйте ещё раз.")

    response = RedirectResponse(
        _safe_next(request.cookies.get(NEXT_COOKIE)), status_code=303
    )
    auth_service.set_auth_cookies(response, session)
    _clear_cookies(response)
    return response
