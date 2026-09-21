import httpx
from fastapi import Request
from postgrest.exceptions import APIError

from app.services import auth as auth_service

ACHIEVEMENT_ERRORS = (
    APIError,
    httpx.HTTPError,
    auth_service.SupabaseNotConfiguredError,
)

ACHIEVEMENT_SELECT = (
    "*, category:categories(id,name,slug), "
    "creator:profiles!achievements_creator_id_fkey("
    "id,username,display_name,avatar_url)"
)
CATEGORY_SELECT = "id,name,slug"
PROFILE_SELECT = "id,username,display_name,avatar_url,role,created_at"

DEFAULT_LIMIT = 60
SORT_RANK = "rank"
SORT_NEW = "new"
ALLOWED_SORTS = (SORT_RANK, SORT_NEW)


def list_categories() -> list[dict]:
    client = auth_service.new_anon_client()
    res = client.table("categories").select(CATEGORY_SELECT).order("name").execute()
    return res.data or []


def _category_id_by_slug(client, slug: str) -> int | None:
    res = (
        client.table("categories")
        .select("id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if res is None or not res.data:
        return None
    return res.data["id"]


def list_achievements(
    *,
    category_slug: str | None = None,
    sort: str = SORT_RANK,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    client = auth_service.new_anon_client()
    query = (
        client.table("achievements")
        .select(ACHIEVEMENT_SELECT)
        .eq("status", "published")
    )
    if category_slug:
        category_id = _category_id_by_slug(client, category_slug)
        if category_id is None:
            return []
        query = query.eq("category_id", category_id)
    if sort == SORT_NEW:
        query = query.order("created_at", desc=True)
    else:
        query = query.order("rank").order("created_at", desc=True)
    res = query.limit(limit).execute()
    return res.data or []


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


def list_achievements_by_creator(request: Request, creator_id: str) -> list[dict]:
    client = auth_service.client_for_request(request)
    res = (
        client.table("achievements")
        .select(ACHIEVEMENT_SELECT)
        .eq("creator_id", creator_id)
        .order("created_at", desc=True)
        .limit(DEFAULT_LIMIT)
        .execute()
    )
    return res.data or []


def get_profile(request: Request, username: str) -> dict | None:
    client = auth_service.client_for_request(request)
    res = (
        client.table("profiles")
        .select(PROFILE_SELECT)
        .ilike("username", username)
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
