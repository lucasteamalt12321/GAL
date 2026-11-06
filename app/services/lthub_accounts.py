"""LTHub OAuth accounts: связь внешнего identity с Supabase-пользователем.

LTHub не отдаёт пароль и не хранит GAL-сессии. Мы создаём (или находим)
служебного Supabase-пользователя с детерминированным email, привязываем его к
`external_identities` и выдаём обычную cookie-сессию Supabase Auth.
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


def _username_for(subject: str) -> str:
    candidate = f"lthub_{subject}"
    if len(candidate) <= 32:
        return candidate
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:20]
    return f"lthub_{digest}"


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


def _create_or_reuse_user(
    service, *, subject: str, login: str, display_name: str
) -> str:
    email = _email_for(subject)
    password = secrets.token_urlsafe(32)
    username = _username_for(subject)
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
        # детерминированному username и переиспользуем.
        user_id = _find_profile_by_username(service, username)
        if user_id is None:
            raise LthubAccountError(
                "Не удалось создать аккаунт GAL для LTHub."
            ) from exc

    service.table("profiles").update({"display_name": display, "avatar_url": None}).eq(
        "id", user_id
    ).execute()
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


def _find_profile_by_username(service, username: str) -> str | None:
    res = (
        service.table("profiles")
        .select("id")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    if res.data:
        return str(res.data[0]["id"])
    return None


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
    login = (login or "lthub").strip()[:64]
    display_name = (display_name or "").strip()[:64]
    service = auth_service.new_service_client()
    password = secrets.token_urlsafe(32)

    profile_id = _find_identity(service, subject)
    if profile_id is not None:
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
