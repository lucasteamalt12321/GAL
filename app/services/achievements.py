import httpx
from fastapi import Request
from postgrest.exceptions import APIError

from app.services import auth as auth_service

ACHIEVEMENT_ERRORS = (
    APIError,
    httpx.HTTPError,
    auth_service.SupabaseNotConfiguredError,
    # create_achievement сам бросает PermissionError, когда user-JWT клиент
    # недоступен (битая сессия) — раньше это давало 500 вместо формы с ошибкой.
    PermissionError,
)

ACHIEVEMENT_SELECT = (
    "*, category:categories(id,name,slug), "
    "creator:profiles!achievements_creator_id_fkey("
    "id,username,display_name,avatar_url)"
)
CATEGORY_SELECT = "id,name,slug"
PROFILE_SELECT = "id,username,display_name,avatar_url,role,created_at"

DEFAULT_LIMIT = 60
PAGE_SIZE = 24
SORT_RANK = "rank"
SORT_NEW = "new"
ALLOWED_SORTS = (SORT_RANK, SORT_NEW)
CREATOR_VISIBLE_STATUSES = ("published", "pending")


def list_categories() -> list[dict]:
    client = auth_service.new_anon_client()
    res = client.table("categories").select(CATEGORY_SELECT).order("name").execute()
    return res.data or []


def get_category_by_slug(slug: str) -> dict | None:
    client = auth_service.new_anon_client()
    res = (
        client.table("categories")
        .select(CATEGORY_SELECT)
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if res is None:
        return None
    return res.data


def list_achievements(
    *,
    category_slug: str | None = None,
    sort: str = SORT_RANK,
    page: int = 1,
    limit: int = PAGE_SIZE,
    query: str | None = None,
) -> tuple[list[dict], int]:
    """Возвращает (страница достижений, всего найдено)."""
    client = auth_service.new_anon_client()
    base = (
        client.table("achievements")
        .select(ACHIEVEMENT_SELECT, count="exact")
        .eq("status", "published")
    )
    if category_slug:
        category = get_category_by_slug(category_slug)
        if category is None:
            return [], 0
        base = base.eq("category_id", category["id"])
    if query:
        base = base.ilike("title", f"%{auth_service.ilike_literal(query)}%")
    if sort == SORT_NEW:
        base = base.order("created_at", desc=True)
    else:
        base = base.order("rank").order("created_at", desc=True)
    page = max(1, page)
    start = (page - 1) * limit
    res = base.range(start, start + limit - 1).execute()
    total = res.count if res.count is not None else len(res.data or [])
    return res.data or [], total


def get_achievement(request: Request, achievement_id: int) -> dict | None:
    client = auth_service.client_for_request(request)
    res = (
        client.table("achievements")
        .select(ACHIEVEMENT_SELECT)
        .eq("id", achievement_id)
        .maybe_single()
        .execute()
    )
    if res is None:
        return None
    return res.data


def list_achievements_by_creator(
    request: Request,
    creator_id: str,
    *,
    statuses: tuple[str, ...] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    client = auth_service.client_for_request(request)
    query = (
        client.table("achievements")
        .select(ACHIEVEMENT_SELECT)
        .eq("creator_id", creator_id)
    )
    if statuses:
        query = query.in_("status", list(statuses))
    res = query.order("created_at", desc=True).limit(limit).execute()
    return res.data or []


def get_profile(request: Request, username: str) -> dict | None:
    client = auth_service.client_for_request(request)
    res = (
        client.table("profiles")
        .select(PROFILE_SELECT)
        .ilike("username", auth_service.ilike_literal(username))
        .maybe_single()
        .execute()
    )
    if res is None:
        return None
    return res.data


def create_achievement(
    request: Request,
    *,
    title: str,
    description: str,
    requirements: str,
    category_id: int | None,
) -> dict:
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise PermissionError("Authentication required")
    payload: dict = {
        "title": title,
        "description": description,
        "requirements": requirements,
        "creator_id": user.id,
        "status": "pending",
    }
    if category_id is not None:
        payload["category_id"] = category_id
    res = client.table("achievements").insert(payload).execute()
    return res.data[0]


def update_achievement(
    request: Request,
    achievement_id: int,
    *,
    title: str,
    description: str,
    requirements: str,
    category_id: int | None,
) -> dict:
    """Правит только своё достижение в статусе pending (RLS + фильтр)."""
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise PermissionError("Authentication required")
    payload: dict = {
        "title": title,
        "description": description,
        "requirements": requirements,
        "category_id": category_id,
    }
    res = (
        client.table("achievements")
        .update(payload)
        .eq("id", achievement_id)
        .eq("creator_id", user.id)
        .eq("status", "pending")
        .execute()
    )
    if not res.data:
        raise PermissionError("Достижение не найдено или уже опубликовано.")
    return res.data[0]


def soft_delete_own_pending(request: Request, achievement_id: int) -> None:
    """Скрывает своё достижение, пока оно на модерации (status -> deleted)."""
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        raise PermissionError("Authentication required")
    res = (
        client.table("achievements")
        .update({"status": "deleted"})
        .eq("id", achievement_id)
        .eq("creator_id", user.id)
        .eq("status", "pending")
        .execute()
    )
    if not res.data:
        raise PermissionError("Достижение не найдено или уже рассмотрено.")
