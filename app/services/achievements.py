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


def _category_filter(slug: str | None) -> int | None:
    if not slug:
        return None
    category = get_category_by_slug(slug)
    return category["id"] if category else None


def list_achievements(
    *,
    category_slug: str | None = None,
    sort: str = SORT_RANK,
    page: int = 1,
    limit: int = PAGE_SIZE,
    query: str | None = None,
) -> tuple[list[dict], int]:
    """Возвращает (страница достижений, всего найдено).

    Сначала отдельно запрашивается count, затем данные: PostgREST отвечает
    416 на range за пределами выборки, а 416 нельзя отличить от сбоя БД
    в глобальном обработчике.

    В список попадают опубликованные достижения с любым ranking_status:
    - ranked (3+ оценок) — с рангом в топе
    - unknown (1-2 оценки) — между ranked, но не влияют на нумерацию топа
    """
    client = auth_service.new_anon_client()
    category_id = _category_filter(category_slug)
    if category_slug and category_id is None:
        return [], 0

    def _apply_filters(request):
        request = request.eq("status", "published")
        if category_id is not None:
            request = request.eq("category_id", category_id)
        if query:
            request = request.ilike("title", f"%{auth_service.ilike_literal(query)}%")
        return request

    # Подсчет всех опубликованных достижений (включая unknown)
    count_res = (
        _apply_filters(client.table("achievements").select("id", count="exact"))
        .limit(1)
        .execute()
    )
    total = count_res.count if count_res.count is not None else 0
    page = max(1, page)
    start = (page - 1) * limit
    if start >= total:
        return [], total

    data_query = _apply_filters(client.table("achievements").select(ACHIEVEMENT_SELECT))

    # Получаем все данные без range (для сортировки в Python)
    all_res = data_query.execute()
    all_achievements = all_res.data or []

    # Сортировка в Python
    if sort == SORT_NEW:
        # Сначала ranked по created_at desc, потом unknown по created_at desc
        # ranked идет первым в сортировке (ranking_status < 'unknown' lexicographically)
        all_achievements.sort(
            key=lambda a: (
                a.get("ranking_status") or "unknown",
                a.get("created_at") or "",
            )
        )
        all_achievements.reverse()  # reverse чтобы created_at desc
    else:
        # Сортировка по рангу:
        # Сортируем все достижения по позиции (rank для ranked, average_position для unknown).
        # Unknown вставляются между ranked на основе average_position, но не занимают место в rank.
        def sort_key(a):
            if a.get("ranking_status") == "ranked":
                # ranked: используем rank как дробное число для сортировки
                rank = a.get("rank")
                created_at = a.get("created_at") or ""
                if rank is None:
                    return (float("inf"), "ranked", created_at)
                return (float(rank), "ranked", created_at)
            else:
                # unknown: используем average_position для вставки между ranked
                avg_pos = a.get("average_position")
                created_at = a.get("created_at") or ""
                if avg_pos is None:
                    return (float("inf"), "unknown", created_at)
                return (float(avg_pos), "unknown", created_at)

        all_achievements.sort(key=sort_key)

    # Применяем pagination
    paginated = all_achievements[start : start + limit]
    return paginated, total


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


CREATE_REQUIRES_EVALUATION = (
    "Сначала пройдите и оцените хотя бы одно существующее достижение — "
    "после этого можно предложить своё."
)


def has_user_evaluation(request: Request) -> bool:
    """Есть ли у пользователя оценка хотя бы одного опубликованного достижения.

    Оценки удалённых или скрытых достижений не считаются: строки в
    ``evaluations`` остаются, но «пройти существующее достижение» уже нельзя.
    """
    user = request.state.user
    client = auth_service.user_client_from_request(request)
    if user is None or client is None:
        return False

    evaluated = (
        client.table("evaluations")
        .select("achievement_id")
        .eq("user_id", user.id)
        .execute()
    )
    achievement_ids = {
        row["achievement_id"]
        for row in (evaluated.data or [])
        if row.get("achievement_id") is not None
    }
    if not achievement_ids:
        return False

    published = (
        client.table("achievements")
        .select("id")
        .in_("id", sorted(achievement_ids))
        .eq("status", "published")
        .execute()
    )
    return bool(published.data)


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
    # Defense in depth: правило проверяется и в роутере, но обойти его через
    # другой вызов сервиса тоже нельзя.
    if not has_user_evaluation(request):
        raise PermissionError(CREATE_REQUIRES_EVALUATION)
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
