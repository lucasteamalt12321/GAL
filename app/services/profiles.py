"""Профиль пользователя: чтение, редактирование, смена пароля."""

from __future__ import annotations

from fastapi import Request
from postgrest.exceptions import APIError

from app.services import auth as auth_service

PROFILE_ERRORS = (
    APIError,
    auth_service.SupabaseNotConfiguredError,
)


class ProfileError(Exception):
    pass


def update_profile(
    request: Request,
    *,
    username: str,
    display_name: str,
    avatar_url: str,
) -> dict:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise ProfileError("Требуется вход в систему.")

    username = (username or "").strip()
    display_name = (display_name or "").strip()[:64]
    avatar_url = (avatar_url or "").strip()[:500]

    username_error = auth_service.validate_username(username)
    if username_error:
        raise ProfileError(username_error)
    if avatar_url and not avatar_url.startswith(("http://", "https://")):
        raise ProfileError("Ссылка на аватар должна начинаться с http:// или https://")

    if username.lower() != (user.username or "").lower():
        try:
            taken = (
                client.table("profiles")
                .select("id")
                .ilike("username", auth_service.ilike_literal(username))
                .neq("id", user.id)
                .limit(1)
                .execute()
            )
        except PROFILE_ERRORS as exc:
            raise ProfileError("Не удалось проверить имя пользователя.") from exc
        if taken.data:
            raise ProfileError("Это имя пользователя уже занято. Выберите другое.")

    try:
        res = (
            client.table("profiles")
            .update(
                {
                    "username": username,
                    "display_name": display_name or None,
                    "avatar_url": avatar_url or None,
                }
            )
            .eq("id", user.id)
            .execute()
        )
    except APIError as exc:
        if getattr(exc, "code", None) == "23505":
            raise ProfileError(
                "Это имя пользователя уже занято. Выберите другое."
            ) from exc
        raise ProfileError("Не удалось сохранить профиль. Попробуйте ещё раз.") from exc
    if not res.data:
        raise ProfileError("Не удалось сохранить профиль. Попробуйте ещё раз.")
    return res.data[0]


def change_password(
    request: Request,
    *,
    current_password: str,
    new_password: str,
) -> None:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None or not user.email:
        raise ProfileError("Требуется вход в систему.")
    current_password = current_password or ""
    new_password = new_password or ""
    if len(new_password) < 6:
        raise ProfileError("Новый пароль должен быть не короче 6 символов.")
    if new_password == current_password:
        raise ProfileError("Новый пароль совпадает с текущим.")

    try:
        client.auth.sign_in_with_password(
            {"email": user.email, "password": current_password}
        )
        client.auth.update_user({"password": new_password})
    except auth_service.AUTH_ERRORS as exc:
        message = str(exc)
        if "Invalid" in message or "credentials" in message.lower():
            raise ProfileError("Текущий пароль указан неверно.") from exc
        raise ProfileError(
            "Не удалось изменить пароль. Попробуйте выйти и войти заново."
        ) from exc
