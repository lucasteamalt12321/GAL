"""LTHub OAuth accounts: связь внешнего identity с Supabase-пользователем.

LTHub не отдаёт пароль и не хранит GAL-сессии. Мы создаём (или находим)
служебного Supabase-пользователя с детерминированным email, привязываем его к
`external_identities` и выдаём обычную cookie-сессию Supabase Auth.

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

from app.services import auth as auth_service

IDENTITY_PROVIDER = "lthub"
SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


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


def _find_profile_by_username(service, username: str) -> str | None:
    res = (
        service.table("profiles")
        .select("id")
        .ilike("username", auth_service.ilike_literal(username))
        .limit(1)
        .execute()
    )
    if res.data:
        return str(res.data[0]["id"])
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
        # Пользователь мог остаться от прерванного первого входа — ищем по
        # выбранному username или по старому lthub_<subject>.
        user_id = _find_profile_by_username(
            service, username
        ) or _find_profile_by_username(service, _username_for(subject, ""))
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
    try:
        service.table("external_identities").insert(
            {
                "provider": IDENTITY_PROVIDER,
                "subject": subject,
                "profile_id": user_id,
            }
        ).execute()
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
        try:
            service.auth.admin.update_user_by_id(profile_id, {"password": password})
        except auth_service.AUTH_ERRORS:
            profile_id = None

    if profile_id is None:
        profile_id = _create_or_reuse_user(
            service, subject=subject, login=login, display_name=display_name
        )
        service.auth.admin.update_user_by_id(profile_id, {"password": password})

    email = _email_for(subject)
    try:
        session = auth_service.new_anon_client().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except (auth_service.AUTH_ERRORS, httpx.HTTPError) as exc:
        raise LthubAccountError("Не удалось создать сессию по аккаунту LTHub.") from exc
    if session.session is None:
        raise LthubAccountError("Не удалось создать сессию по аккаунту LTHub.")
    return session.session
