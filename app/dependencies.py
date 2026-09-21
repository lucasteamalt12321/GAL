from fastapi import HTTPException, Request

from app.services import auth as auth_service


def get_optional_user(request: Request) -> auth_service.CurrentUser | None:
    return getattr(request.state, "user", None)


def get_current_user(request: Request) -> auth_service.CurrentUser:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def get_current_moderator(request: Request) -> auth_service.CurrentUser:
    user = get_current_user(request)
    if user.role not in ("moderator", "admin"):
        raise HTTPException(status_code=403, detail="Moderator role required")
    return user


def get_current_admin(request: Request) -> auth_service.CurrentUser:
    user = get_current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
