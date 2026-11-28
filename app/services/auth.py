import re
import time
from dataclasses import dataclass

import httpx
from fastapi import Request, Response
from postgrest.exceptions import APIError
from supabase import Client, ClientOptions, create_client
from supabase_auth.errors import (
    AuthApiError,
    AuthError,
    AuthInvalidCredentialsError,
    AuthInvalidJwtError,
    AuthSessionMissingError,
    AuthWeakPasswordError,
)

from app.config import get_settings
from app.supabase_http import new_supabase_httpx_client

SESSION_COOKIE = "gal_session"
REFRESH_COOKIE = "gal_refresh"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NETWORK_RETRIES = 3
_RATE_LIMIT_CODES = frozenset(
    {
        "over_request_rate_limit",
        "over_email_send_rate_limit",
        "over_sms_send_rate_limit",
    }
)


class SupabaseNotConfiguredError(RuntimeError):
    pass


class LoginLookupUnavailableError(RuntimeError):
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


def _client_options() -> ClientOptions:
    """Опции Supabase-клиента с явным таймаутом PostgREST.

    Без него библиотека ждёт 120 с и бросает httpx.ReadTimeout, из-за чего
    зависший Supabase превращался в 500 на всех БД-страницах.
    """
    return ClientOptions(
        postgrest_client_timeout=get_settings().supabase_timeout_seconds,
        # postgrest-py принудительно включает HTTP/2; в open-runtimes такой
        # соединение зависает до таймаута, хотя HTTP/1.1 отвечает нормально.
        httpx_client=new_supabase_httpx_client(get_settings().supabase_timeout_seconds),
        # Сервер сам управляет refresh через resolve_user. Фоновый auto-refresh
        # библиотеки запускает недемонический Timer на каждый set_session, который
        # ротирует refresh-токен в выброшенном клиенте: браузер остаётся со старым
        # токеном, GoTrue по reuse detection отзывает семейство → разлогин, плюс
        # утечка потоков.
        auto_refresh_token=False,
    )


def new_anon_client() -> Client:
    settings = get_settings()
    if not settings.supabase_configured:
        raise SupabaseNotConfiguredError("Supabase is not configured")
    return create_client(
        settings.supabase_url,
        settings.supabase_anon_key,
        options=_client_options(),
    )


def new_user_client(access_token: str, refresh_token: str) -> Client:
    if not looks_like_jwt(access_token) or not refresh_token:
        raise MalformedSessionTokenError("Malformed session cookie")
    client = new_anon_client()
    client.auth.set_session(access_token, refresh_token)
    return client


def new_service_client() -> Client:
    """Серверный клиент с service role key (обходит RLS).

    Нужен только для фоновых агрегатов (Bradley–Terry): читает все оценки,
    которые не видны обычному пользователю, и применяет результат через
    security definer RPC. Ключ никогда не покидает сервер.
    """
    settings = get_settings()
    if not settings.service_role_configured:
        raise SupabaseNotConfiguredError("Supabase service role is not configured")
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
        options=_client_options(),
    )


def user_client_from_request(request: Request) -> Client | None:
    # resolve_user мог только что обновить сессию и положить новые токены в
    # request.state. В request.cookies при этом остаётся старый access-токен;
    # если взять его, Storage/PostgREST ответят 401 на этом же запросе.
    access = getattr(request.state, "access_token", None) or request.cookies.get(
        SESSION_COOKIE
    )
    refresh = getattr(request.state, "refresh_token", None) or request.cookies.get(
        REFRESH_COOKIE
    )
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


def validate_email(email: str) -> str | None:
    """Проверка формата email до обращения к GoTrue (понятная ошибка на русском)."""
    value = (email or "").strip()
    if not value:
        return "Укажите email."
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        return "Некорректный email. Проверьте адрес (например, name@example.com)."
    return None


LTHUB_SERVICE_EMAIL_RE = re.compile(
    r"^lthub-oauth-[A-Za-z0-9_.-]+@example\.com$", re.IGNORECASE
)


def is_reserved_service_email(email: str) -> bool:
    """Служебный email LTHub-аккаунта нельзя зарегистрировать вручную.

    Иначе атакующий занимает `lthub-oauth-<sub жертвы>@example.com`, и при первом
    входе жертвы через LTHub её identity привяжется к аккаунту атакующего
    (create_user → email_exists → reuse по email).
    """
    return bool(LTHUB_SERVICE_EMAIL_RE.fullmatch((email or "").strip()))


def friendly_auth_error(exc: Exception) -> str:
    """Переводит ошибки Supabase Auth в понятные сообщения на русском.

    GoTrue отдаёт сырой английский текст («For security purposes, you can only
    request this after 20 seconds», «Unable to validate email address…»), который
    раньше показывался пользователю как есть. Здесь известные коды/сообщения
    маппятся в дружелюбные формулировки, неизвестные — в общий текст.
    """
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    raw = getattr(exc, "message", None) or getattr(exc, "msg", None) or str(exc)
    text = (raw or "").lower()

    # GoTrue иногда присылает 5xx/429 с кодом invalid_credentials и текстом
    # «Invalid login credentials». Сбой сервиса нельзя показывать как ошибку
    # введённых данных — statuses проверяются раньше любых текстовых эвристик.
    if isinstance(exc, (httpx.HTTPError, SupabaseNotConfiguredError)) or (
        isinstance(status, int) and status >= 500
    ):
        return "Сервис входа временно недоступен. Попробуйте позже."
    if (
        (isinstance(status, int) and status == 429)
        or code in _RATE_LIMIT_CODES
        or "rate limit" in text
        or "for security purposes" in text
    ):
        match = re.search(r"after\s+(\d+)\s*seconds?", text)
        if match:
            return f"Слишком много попыток. Повторите через {match.group(1)} сек."
        return "Слишком много попыток. Подождите немного и попробуйте снова."
    if (
        code in {"email_address_invalid", "validation_failed"}
        or "unable to validate email" in text
        or ("email" in text and "invalid" in text)
    ):
        return "Некорректный email. Проверьте адрес и попробуйте снова."
    if (
        code in {"email_exists", "user_already_exists"}
        or "already registered" in text
        or "already been registered" in text
    ):
        return "Этот email уже зарегистрирован. Войдите или восстановите пароль."
    if isinstance(exc, AuthWeakPasswordError) or code == "weak_password":
        return "Пароль слишком простой. Используйте не менее 6 символов, буквы и цифры."
    if "password" in text and ("at least" in text or "characters" in text):
        return "Пароль должен быть не короче 6 символов."
    if code == "invalid_credentials" or "invalid login" in text:
        return "Неверный email или пароль."
    if code == "email_not_confirmed" or "email not confirmed" in text:
        return "Email не подтверждён. Перейдите по ссылке из письма."
    if code == "signup_disabled":
        return "Регистрация временно отключена. Попробуйте позже."
    return "Не удалось выполнить запрос. Попробуйте позже."


def is_invalid_credentials_error(exc: Exception) -> bool:
    if isinstance(exc, AuthInvalidCredentialsError):
        return True
    return (
        isinstance(exc, AuthApiError)
        and getattr(exc, "code", None) == "invalid_credentials"
        and getattr(exc, "status", None) in (400, 401)
    )


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


def _lthub_email_for_profile(service: Client, profile_id: str) -> str | None:
    """Служебный email по identity LTHub.

    Сбой запроса — это недоступность сервиса, а не отсутствие identity:
    тихое None здесь приводило к fallback в Admin API и, при 404, к общему
    «Неверный email или пароль» для существующего аккаунта.
    """
    try:
        row = (
            service.table("external_identities")
            .select("subject")
            .eq("provider", "lthub")
            .eq("profile_id", profile_id)
            .limit(1)
            .execute()
        )
    except PROFILE_ERRORS as exc:
        raise LoginLookupUnavailableError("LTHub identity lookup failed") from exc
    if not row.data:
        return None
    subject = str(row.data[0].get("subject") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", subject):
        return None
    return f"lthub-oauth-{subject}@example.com"


def resolve_login_account(identifier: str) -> tuple[str, str] | None:
    """Профиль и email аккаунта по email или никнейму.

    Возвращает пару (profile_id, email) или None, если аккаунт не найден.
    Сбой lookup отличается от неизвестного ника и выбрасывает
    LoginLookupUnavailableError, чтобы форма не показывала «неверный пароль»
    при недоступном Supabase.
    Email не отдаётся клиенту — только серверу.
    Для входа по email profile_id может быть пустым (профиль не искали).
    """
    value = (identifier or "").strip()
    if not value:
        return None
    if "@" in value:
        return "", value
    # PostgREST трактует `*` в ilike как wildcard (%). В username его быть не
    # может (USERNAME_RE), поэтому такой идентификатор ником не является —
    # иначе `Ton*` резолвил бы чужой email и создавал timing-оракул.
    if "*" in value:
        return None
    if not get_settings().service_role_configured:
        raise LoginLookupUnavailableError("Service role is not configured")
    try:
        service = new_service_client()
        row = (
            service.table("profiles")
            .select("id")
            .ilike("username", ilike_literal(value))
            .limit(1)
            .execute()
        )
    except PROFILE_ERRORS as exc:
        raise LoginLookupUnavailableError("Profile lookup failed") from exc
    if not row.data:
        return None
    profile_id = str(row.data[0]["id"])
    email = _lthub_email_for_profile(service, profile_id)
    if email:
        return profile_id, email
    try:
        resp = service.auth.admin.get_user_by_id(profile_id)
    except AuthApiError as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise LoginLookupUnavailableError("Auth user lookup failed") from exc
    except AUTH_ERRORS as exc:
        raise LoginLookupUnavailableError("Auth user lookup failed") from exc
    email = resp.user.email or None
    if not email:
        # Пользователь есть, но email отсутствует — это не «неизвестный аккаунт»,
        # а неполные данные сервиса; общий текст обвинил бы пользователя.
        raise LoginLookupUnavailableError("Auth user has no email")
    return profile_id, email


def resolve_login_email(identifier: str) -> str | None:
    """Email для входа по email или никнейму.

    None означает только «аккаунт неизвестен»; недоступность lookup бросает
    LoginLookupUnavailableError и не подменяется неверными данными.
    """
    account = resolve_login_account(identifier)
    if account is None:
        return None
    return account[1]


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


def _is_invalid_session_error(exc: Exception) -> bool:
    """Явно невалидная сессия (а не транзиентный сбой).

    supabase_auth маппит HTTP-ошибки GoTrue в AuthApiError, поэтому истёкший или
    отозванный refresh-токен приходит как AuthApiError 400/401/403, а не как
    AuthInvalidJwtError/AuthSessionMissingError. Без этого куки мёртвой сессии
    никогда не удалялись и каждая страница анонима делала get_user + refresh.
    AuthRetryableError (5xx) и rate-limit (429) не считаем невалидной сессией.
    """
    if isinstance(
        exc,
        (AuthInvalidJwtError, AuthSessionMissingError, AuthInvalidCredentialsError),
    ):
        return True
    if isinstance(exc, AuthApiError):
        if getattr(exc, "code", None) in _RATE_LIMIT_CODES:
            return False
        return getattr(exc, "status", None) in (400, 401, 403)
    return False


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
    session_invalid = False
    try:
        resp = _retry_network(lambda: client.auth.get_user(jwt=access))
    except MalformedSessionTokenError:
        _drop_auth_cookies(request)
        return None
    except AUTH_ERRORS as exc:
        if _is_invalid_session_error(exc):
            session_invalid = True
        resp = None

    if (resp is None or resp.user is None) and refresh:
        try:
            refreshed = _retry_network(lambda: client.auth.refresh_session(refresh))
        except AUTH_ERRORS as exc:
            if _is_invalid_session_error(exc):
                session_invalid = True
            refreshed = None
        if refreshed is not None and refreshed.session is not None:
            session = refreshed.session
            request.state.access_token = session.access_token
            request.state.refresh_token = session.refresh_token
            request.state.pending_cookies = [
                (SESSION_COOKIE, session.access_token, {}),
                (REFRESH_COOKIE, session.refresh_token, {}),
            ]
            resp = None
            try:
                resp = _retry_network(
                    lambda: client.auth.get_user(jwt=refreshed.session.access_token)
                )
            except AUTH_ERRORS:
                resp = None

    if resp is None or resp.user is None:
        # Удаляем cookies только при явно недействительной сессии. Сетевой
        # сбой Supabase не должен разлогинивать пользователя навсегда: на
        # следующем запросе токены ещё могут обновиться.
        if session_invalid:
            _drop_auth_cookies(request)
        return None
    profile = _fetch_profile(client, str(resp.user.id))
    return _build_user(resp.user, profile)
