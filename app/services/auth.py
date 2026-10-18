import re
import time
from dataclasses import dataclass

import httpx
from fastapi import Request, Response
from postgrest.exceptions import APIError
from supabase import Client, create_client
from supabase_auth.errors import (
    AuthApiError,
    AuthError,
    AuthInvalidCredentialsError,
    AuthInvalidJwtError,
    AuthSessionMissingError,
    AuthWeakPasswordError,
)

from app.config import get_settings

SESSION_COOKIE = "gal_session"
REFRESH_COOKIE = "gal_refresh"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_NETWORK_RETRIES = 3


class SupabaseNotConfiguredError(RuntimeError):
    pass


class MalformedSessionTokenError(ValueError):
    """Кука сессии не похожа на JWT (битая/обрезанная/подменённая)."""


AUTH_ERRORS = (
    AuthError,
    httpx.HTTPError,
    SupabaseNotConfiguredError,
    MalformedSessionTokenError,
)
PROFILE_ERRORS = (APIError, httpx.HTTPError)

# AuthRetryableError (5xx/503 от GoTrue) — подкласс AuthError, но ретраится.
# Остальные AuthError (невалидный токен, слабый пароль) — нет.
NON_RETRYABLE_AUTH_ERRORS = (
    AuthApiError,
    AuthSessionMissingError,
    AuthInvalidJwtError,
    AuthInvalidCredentialsError,
    AuthWeakPasswordError,
    MalformedSessionTokenError,
    SupabaseNotConfiguredError,
)


@dataclass
class CurrentUser:
    id: str
    email: str | None
    username: str | None
    display_name: str | None
    avatar_url: str | None
    role: str


def looks_like_jwt(token: str | None) -> bool:
    """Грубая проверка формы access-токена (3 части, разделённые точкой).

    supabase_auth `set_session` обращается к `access_token.split(".")[1]` и на
    значении без точки бросает IndexError, который не входит ни в один
    перехватываемый кортеж и приводил к 500 на всех страницах при битой куке.
    """
    if not token:
        return False
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def new_anon_client() -> Client:
    settings = get_settings()
    if not settings.supabase_configured:
        raise SupabaseNotConfiguredError("Supabase is not configured")
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def new_user_client(access_token: str, refresh_token: str) -> Client:
    if not looks_like_jwt(access_token) or not refresh_token:
        raise MalformedSessionTokenError("Malformed session cookie")
    client = new_anon_client()
    client.auth.set_session(access_token, refresh_token)
    return client


def user_client_from_request(request: Request) -> Client | None:
    access = request.cookies.get(SESSION_COOKIE)
    refresh = request.cookies.get(REFRESH_COOKIE)
    if not access or not refresh:
        return None
    if not looks_like_jwt(access):
        return None
    try:
        return new_user_client(access, refresh)
    except AUTH_ERRORS:
        return None


def client_for_request(request: Request) -> Client:
    return user_client_from_request(request) or new_anon_client()


def _cookie_kwargs() -> dict:
    settings = get_settings()
    return {
        "max_age": AUTH_COOKIE_MAX_AGE,
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": settings.is_production,
    }


def validate_username(username: str) -> str | None:
    """Проверка формата username. Возвращает сообщение об ошибке или None."""
    value = (username or "").strip()
    if not (3 <= len(value) <= 32):
        return "Имя пользователя должно быть от 3 до 32 символов."
    if not USERNAME_RE.fullmatch(value):
        return "Имя пользователя может содержать только латиницу, цифры и ., _, -."
    return None


def ilike_literal(value: str) -> str:
    """Экранирование для сравнения ILIKE как литерала.

    PostgREST не позволяет задать ESCAPE-clause, поэтому экранируем wildcard
    % и _ обратным слешем (стандартный escape-символ PostgreSQL в LIKE).
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def username_available(username: str) -> bool:
    """Проверка занятости username (по регистронезависимому индексу)."""
    try:
        client = new_anon_client()
        row = (
            client.table("profiles")
            .select("id")
            .ilike("username", ilike_literal((username or "").strip()))
            .maybe_single()
            .execute()
        )
    except PROFILE_ERRORS:
        return True
    return row is None or not row.data


def set_auth_cookies(response: Response, session) -> None:
    kwargs = _cookie_kwargs()
    response.set_cookie(SESSION_COOKIE, session.access_token, **kwargs)
    response.set_cookie(REFRESH_COOKIE, session.refresh_token, **kwargs)


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


def _fetch_profile(client: Client, user_id: str) -> dict | None:
    try:
        row = (
            client.table("profiles")
            .select("*")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
    except PROFILE_ERRORS:
        return None
    if row is None:
        return None
    return row.data


def _build_user(resp_user, profile: dict | None) -> CurrentUser | None:
    if resp_user is None:
        return None
    if profile:
        role = profile.get("role") or "user"
        username = profile.get("username")
        display_name = profile.get("display_name")
        avatar_url = profile.get("avatar_url")
    else:
        role, username, display_name, avatar_url = "user", None, None, None
    return CurrentUser(
        id=str(resp_user.id),
        email=resp_user.email,
        username=username,
        display_name=display_name,
        avatar_url=avatar_url,
        role=role,
    )


def _retry_network(fn):
    """Повторяет вызовы auth при транзиентных ошибках.

    AuthRetryableError (5xx/503 GoTrue) — подкласс AuthError, поэтому blanket
    `except AuthError: raise` ретраил 0 раз, а неретраибельный
    SupabaseNotConfiguredError ретраился трижды с задержками. Теперь разделены.
    """
    last: Exception | None = None
    for attempt in range(_NETWORK_RETRIES):
        try:
            return fn()
        except NON_RETRYABLE_AUTH_ERRORS:
            raise
        except AUTH_ERRORS as exc:
            last = exc
            if attempt + 1 < _NETWORK_RETRIES:
                time.sleep(0.3 * (attempt + 1))
    raise last  # type: ignore[misc]


def _drop_auth_cookies(request: Request) -> None:
    """Помечает куки сессии на удаление в ответе.

    Без этого битая кука переживала запрос: каждая следующая страница снова
    делала get_user + refresh (до 3 попыток с задержкой).
    """
    cookies = getattr(request.state, "pending_cookies", None)
    if cookies is None:
        return
    if any(name == SESSION_COOKIE for name, _value, _kwargs in cookies):
        return
    cookies.append((SESSION_COOKIE, "", {"max_age": 0, "path": "/"}))
    cookies.append((REFRESH_COOKIE, "", {"max_age": 0, "path": "/"}))


def resolve_user(request: Request) -> CurrentUser | None:
    access = request.cookies.get(SESSION_COOKIE)
    refresh = request.cookies.get(REFRESH_COOKIE)
    if not access:
        return None
    if not looks_like_jwt(access):
        _drop_auth_cookies(request)
        return None
    settings = get_settings()
    if not settings.supabase_configured:
        return None

    client = new_anon_client()
    resp = None
    try:
        resp = _retry_network(lambda: client.auth.get_user(jwt=access))
    except MalformedSessionTokenError:
        _drop_auth_cookies(request)
        return None
    except AUTH_ERRORS:
        resp = None

    if (resp is None or resp.user is None) and refresh:
        try:
            refreshed = _retry_network(lambda: client.auth.refresh_session(refresh))
        except NON_RETRYABLE_AUTH_ERRORS:
            refreshed = None
        except AUTH_ERRORS:
            refreshed = None
        if refreshed is not None and refreshed.session is not None:
            request.state.pending_cookies = [
                (SESSION_COOKIE, refreshed.session.access_token, {}),
                (REFRESH_COOKIE, refreshed.session.refresh_token, {}),
            ]
            resp = None
            try:
                resp = _retry_network(
                    lambda: client.auth.get_user(jwt=refreshed.session.access_token)
                )
            except AUTH_ERRORS:
                resp = None

    if resp is None or resp.user is None:
        _drop_auth_cookies(request)
        return None
    profile = _fetch_profile(client, str(resp.user.id))
    return _build_user(resp.user, profile)
