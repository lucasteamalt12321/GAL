"""LTHub OAuth accounts: связь внешнего identity с Supabase-пользователем.

LTHub не отдаёт пароль и не хранит GAL-сессии. Мы создаём (или находим)
служебного Supabase-пользователя с детерминированным email, привязываем его к
`external_identities` и выдаём обычную cookie-сессию Supabase Auth.

Пароль. Для аккаунта, зарегистрированного через LTHub, пароль — это пароль
в LTHub: GAL проверяет его через API LTHub (`/api/auth/login`) и принимает как
пароль аккаунта. Пока пользовательский пароль не подтверждён, у служебного
Supabase-пользователя лежит случайный токен (его можно перевыпустить при
OAuth-входе); после подтверждения флаг `password_synced` поднимается, и
OAuth-вход пароль больше не затирает — иначе пароль LTHub перестал бы работать
после каждого входа через LTHub.

Username берётся из логина LTHub (например, `shadowraven`), а не из внутреннего
subject. Если логин занят в GAL, добавляется суффикс; `lthub_<subject>` —
только последний fallback.
"""

from __future__ import annotations

import hashlib
import re
import secrets

import httpx
from fastapi import Request

from app.config import get_settings
from app.services import auth as auth_service

IDENTITY_PROVIDER = "lthub"
SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
LTHUB_EMAIL_RE = re.compile(
    r"^lthub-oauth-([A-Za-z0-9_.-]+)@example\.com$", re.IGNORECASE
)

# Результаты проверки пароля в LTHub.
PASSWORD_OK = "ok"
PASSWORD_REJECTED = "rejected"
PASSWORD_UNAVAILABLE = "unavailable"
PASSWORD_RATE_LIMITED = "rate_limited"


class LthubAccountError(Exception):
    pass


def _email_for(subject: str) -> str:
    return f"lthub-oauth-{subject}@example.com"


def _sanitize_username(value: str) -> str | None:
    """Приводит ник LTHub к допустимому username GAL (3–32, ascii)."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", (value or "").strip())
    if not (3 <= len(cleaned) <= 32):
        return None
    if not USERNAME_RE.fullmatch(cleaned):
        return None
    return cleaned


def _username_for(subject: str, login: str = "") -> str:
    """Username по умолчанию: логин LTHub, иначе lthub_<subject>."""
    candidate = _sanitize_username(login)
    if candidate:
        return candidate
    fallback = f"lthub_{subject}"
    if len(fallback) <= 32:
        return fallback
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:20]
    return f"lthub_{digest}"


def _username_candidates(subject: str, login: str) -> list[str]:
    candidates: list[str] = []
    base = _sanitize_username(login)
    if base:
        candidates.append(base)
        suffixed = f"{base[:24]}_{subject}"[:32]
        if suffixed not in candidates:
            candidates.append(suffixed)
    fallback = _username_for(subject, "")
    if fallback not in candidates:
        candidates.append(fallback)
    return candidates


def _validate_subject(subject: str) -> str:
    subject = (subject or "").strip()
    if not SUBJECT_RE.fullmatch(subject):
        raise LthubAccountError("LTHub вернул некорректный идентификатор пользователя.")
    return subject


def _find_identity(service, subject: str) -> str | None:
    res = (
        service.table("external_identities")
        .select("profile_id")
        .eq("provider", IDENTITY_PROVIDER)
        .eq("subject", subject)
        .limit(1)
        .execute()
    )
    if res.data:
        return str(res.data[0]["profile_id"])
    return None


def find_identity_by_profile(
    service, profile_id: str, *, strict: bool = False
) -> dict | None:
    """Identity LTHub по профилю GAL (`subject`, `lthub_login`, `password_synced`)."""
    try:
        res = (
            service.table("external_identities")
            .select("profile_id,subject,lthub_login,password_synced")
            .eq("provider", IDENTITY_PROVIDER)
            .eq("profile_id", profile_id)
            .limit(1)
            .execute()
        )
    except auth_service.PROFILE_ERRORS as exc:
        if strict:
            raise auth_service.LoginLookupUnavailableError(
                "LTHub identity lookup failed"
            ) from exc
        return None
    return _identity_row(res.data)


def find_identity_by_subject(
    service, subject: str, *, strict: bool = False
) -> dict | None:
    """Identity LTHub по subject (внутренний id пользователя LTHub)."""
    try:
        res = (
            service.table("external_identities")
            .select("profile_id,subject,lthub_login,password_synced")
            .eq("provider", IDENTITY_PROVIDER)
            .eq("subject", subject)
            .limit(1)
            .execute()
        )
    except auth_service.PROFILE_ERRORS as exc:
        if strict:
            raise auth_service.LoginLookupUnavailableError(
                "LTHub identity lookup failed"
            ) from exc
        return None
    return _identity_row(res.data)


def _identity_row(data) -> dict | None:
    if not data:
        return None
    row = data[0]
    return {
        "profile_id": str(row.get("profile_id") or ""),
        "subject": str(row.get("subject") or ""),
        "lthub_login": str(row.get("lthub_login") or ""),
        "password_synced": bool(row.get("password_synced")),
    }


def find_identity_for_login(
    service, *, profile_id: str, email: str, strict: bool = False
) -> dict | None:
    """Identity LTHub для входа: по профилю, иначе по служебному email."""
    if profile_id:
        identity = find_identity_by_profile(service, profile_id, strict=strict)
        if identity:
            return identity
    match = LTHUB_EMAIL_RE.match((email or "").strip())
    if match:
        return find_identity_by_subject(service, match.group(1), strict=strict)
    return None


def store_lthub_login(service, subject: str, login: str) -> None:
    """Запоминает логин LTHub (нужен для проверки пароля через API LTHub)."""
    value = (login or "").strip()[:64]
    if not value:
        return
    try:
        service.table("external_identities").update({"lthub_login": value}).eq(
            "provider", IDENTITY_PROVIDER
        ).eq("subject", subject).execute()
    except Exception:  # noqa: BLE001 - не блокируем вход из-за служебной записи
        return


def mark_password_synced(service, subject: str, synced: bool = True) -> None:
    """Отмечает, что пароль служебного пользователя — пользовательский."""
    try:
        service.table("external_identities").update({"password_synced": synced}).eq(
            "provider", IDENTITY_PROVIDER
        ).eq("subject", subject).execute()
    except Exception:  # noqa: BLE001 - флаг только оптимизирует OAuth-вход
        return


def mirror_password(service, profile_id: str, subject: str, password: str) -> bool:
    """Делает пароль аккаунта GAL равным подтверждённому паролю LTHub.

    Возвращает False, если Supabase не принял новый пароль (тогда вход
    продолжится по старому паролю — если он совпадает).
    """
    if not password:
        return False
    try:
        service.auth.admin.update_user_by_id(profile_id, {"password": password})
    except auth_service.AUTH_ERRORS:
        return False
    mark_password_synced(service, subject, True)
    return True


def login_endpoint() -> str:
    settings = get_settings()
    explicit = settings.lthub_login_url.strip()
    if explicit:
        return explicit
    base = settings.lthub_api_base_url.strip().rstrip("/")
    return f"{base}/api/auth/login"


def _subjects_match(left: str, right: str) -> bool:
    """Сравнение subject LTHub и user_id из ответа API (8 == "8")."""
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return a.casefold() == b.casefold()


def verify_password(
    login: str, password: str, *, expected_subject: str | None = None
) -> str:
    """Проверяет пароль пользователя в LTHub через API LTHub.

    Возвращает PASSWORD_OK, PASSWORD_REJECTED (LTHub ответил «неверный логин
    или пароль») или PASSWORD_UNAVAILABLE (LTHub недоступен — сеть, таймаут,
    5xx). Недоступность отличается от отказа: в первом случае локальная копия
    пароля в GAL остаётся рабочей.
    """
    settings = get_settings()
    if not settings.lthub_password_check_enabled:
        return PASSWORD_UNAVAILABLE
    login = (login or "").strip()
    if not login or not password:
        return PASSWORD_REJECTED
    try:
        response = httpx.post(
            login_endpoint(),
            json={"login": login, "password": password},
            timeout=settings.lthub_request_timeout_seconds,
        )
    except httpx.HTTPError:
        return PASSWORD_UNAVAILABLE
    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError:
            return PASSWORD_UNAVAILABLE
        if not isinstance(payload, dict):
            return PASSWORD_UNAVAILABLE
        user_id = payload.get("user_id")
        # 200 без user_id — аномалия ответа, а не подтверждение пароля:
        # раньше она открывала вход под чужим логином и записывала пароль
        # в GAL. Недоступность проверки не должна выглядеть как успех.
        if user_id is None or not str(user_id).strip():
            return PASSWORD_UNAVAILABLE
        if not expected_subject:
            return PASSWORD_UNAVAILABLE
        if not _subjects_match(str(user_id), expected_subject):
            # Пароль подошёл другому аккаунту LTHub — не пускаем.
            return PASSWORD_REJECTED
        return PASSWORD_OK
    if response.status_code == 401:
        return PASSWORD_REJECTED
    if response.status_code == 429:
        return PASSWORD_RATE_LIMITED
    return PASSWORD_UNAVAILABLE


def _find_user_id_by_email(service, email: str) -> str | None:
    """Ищет auth-пользователя по детерминированному email LTHub.

    Нельзя искать профиль по username: имя изменяемо и может совпасть с
    чужим аккаунтом (включая гонку между проверкой занятости и созданием).
    Email `lthub-oauth-<subject>@example.com` однозначно принадлежит subject.
    """
    target = email.strip().lower()
    if not target:
        return None
    page = 1
    while page <= 10:
        users = service.auth.admin.list_users(page=page, per_page=1000)
        if not users:
            return None
        for user in users:
            if str(getattr(user, "email", "") or "").strip().lower() == target:
                return str(user.id)
        if len(users) < 1000:
            return None
        page += 1
    return None


def _username_taken(service, username: str, *, exclude_id: str | None = None) -> bool:
    query = (
        service.table("profiles")
        .select("id")
        .ilike("username", auth_service.ilike_literal(username))
        .limit(1)
    )
    if exclude_id:
        query = query.neq("id", exclude_id)
    res = query.execute()
    return bool(res.data)


def _pick_username(
    service, *, subject: str, login: str, exclude_id: str | None = None
) -> str:
    for candidate in _username_candidates(subject, login):
        if not _username_taken(service, candidate, exclude_id=exclude_id):
            return candidate
    return _username_for(subject, "")


def _sync_profile(
    service,
    *,
    profile_id: str,
    subject: str,
    login: str,
    display_name: str,
) -> None:
    """Обновляет display_name и подтягивает ник LTHub для старых аккаунтов."""
    res = (
        service.table("profiles")
        .select("id,username")
        .eq("id", profile_id)
        .maybe_single()
        .execute()
    )
    current = None if res is None else res.data
    if not current:
        return
    display = (display_name or login or "").strip()[:64]
    payload: dict = {"display_name": display or None}
    current_username = str(current.get("username") or "")
    default_username = _username_for(subject, "")
    if current_username.lower() == default_username.lower():
        candidate = _username_for(subject, login)
        if candidate.lower() != current_username.lower() and not _username_taken(
            service, candidate, exclude_id=profile_id
        ):
            payload["username"] = candidate
    service.table("profiles").update(payload).eq("id", profile_id).execute()


def _create_or_reuse_user(
    service, *, subject: str, login: str, display_name: str
) -> str:
    email = _email_for(subject)
    password = secrets.token_urlsafe(32)
    username = _pick_username(service, subject=subject, login=login)
    display = (display_name or login or "").strip()[:64] or username

    try:
        created = service.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "username": username,
                    "display_name": display,
                },
            }
        )
        user_id = str(created.user.id)
    except auth_service.AUTH_ERRORS as exc:
        # Пользователь мог остаться от прерванного первого входа. Ищем только
        # по email, который детерминированно связан с subject: переиспользование
        # профиля по username позволило бы войти в чужой аккаунт.
        user_id = _find_user_id_by_email(service, email)
        if user_id is None:
            raise LthubAccountError(
                "Не удалось создать аккаунт GAL для LTHub."
            ) from exc

    _sync_profile(
        service,
        profile_id=user_id,
        subject=subject,
        login=login,
        display_name=display,
    )
    identity_row = {
        "provider": IDENTITY_PROVIDER,
        "subject": subject,
        "profile_id": user_id,
        "lthub_login": (login or "").strip()[:64] or None,
    }
    try:
        service.table("external_identities").insert(identity_row).execute()
    except Exception:  # noqa: BLE001 - вставка может упасть по любой причине
        # Колонка lthub_login появилась только в миграции 033 — на старой схеме
        # повторяем вставку без неё.
        legacy_row = {k: v for k, v in identity_row.items() if k != "lthub_login"}
        try:
            service.table("external_identities").insert(legacy_row).execute()
        except Exception:
            existing = _find_identity(service, subject)
            if existing is not None:
                user_id = existing
            else:
                raise
    return user_id


def login_with_lthub(
    request: Request,
    *,
    subject: str,
    login: str,
    display_name: str,
):
    """Возвращает Supabase-сессию для LTHub-пользователя."""
    settings = auth_service.get_settings()
    if not settings.service_role_configured:
        raise LthubAccountError("Вход через LTHub временно недоступен.")

    subject = _validate_subject(subject)
    login = (login or "").strip()[:64]
    display_name = (display_name or "").strip()[:64]
    service = auth_service.new_service_client()
    password = secrets.token_urlsafe(32)

    profile_id = _find_identity(service, subject)
    if profile_id is not None:
        _sync_profile(
            service,
            profile_id=profile_id,
            subject=subject,
            login=login,
            display_name=display_name,
        )
        identity = find_identity_by_profile(service, profile_id)
        # Пароль, подтверждённый пользователем, не перевыпускаем: иначе вход
        # через LTHub стирал бы пароль LTHub после каждого входа.
        if not (identity and identity.get("password_synced")):
            try:
                service.auth.admin.update_user_by_id(profile_id, {"password": password})
            except auth_service.AUTH_ERRORS:
                profile_id = None
        store_lthub_login(service, subject, login)

    if profile_id is None:
        profile_id = _create_or_reuse_user(
            service, subject=subject, login=login, display_name=display_name
        )
        service.auth.admin.update_user_by_id(profile_id, {"password": password})
        store_lthub_login(service, subject, login)

    email = _email_for(subject)
    try:
        session = auth_service.new_anon_client().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except auth_service.AUTH_ERRORS as exc:
        # AUTH_ERRORS — кортеж классов; httpx.HTTPError в него уже входит.
        # Список вида (AUTH_ERRORS, httpx.HTTPError) ломал обработку: Python
        # ждал классы, а получал кортеж → TypeError вместо LthubAccountError.
        raise LthubAccountError("Не удалось создать сессию по аккаунту LTHub.") from exc
    if session.session is None:
        raise LthubAccountError("Не удалось создать сессию по аккаунту LTHub.")
    return session.session
