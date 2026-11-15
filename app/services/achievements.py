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

# Вьюха эффективного топа (миграция 027). До неё каталог работает по таблице,
# поэтому все обращения к ней терпимы к отсутствию отношения.
VIEW_NAME = "public_achievements"
VIEW_SELECT = "*"
VIEW_RANK_FIELDS = (
    "rank",
    "permanent_rank",
    "is_temporary",
    "in_leaderboard",
    "ranking_status",
    "average_position",
    "bt_strength",
    "bt_rating",
    "bt_comparisons",
    "bt_rating_se",
    "bt_rating_ci_low",
    "bt_rating_ci_high",
)


def _is_missing_view(exc: APIError) -> bool:
    """Отсутствие вьюхи, а не неизвестная колонка (PGRST204)."""
    code = str(getattr(exc, "code", "") or "").upper()
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'details', '')}".lower()
    if code == "PGRST205" or "pgrst205" in text:
        return True
    if code == "PGRST204" or "column" in text:
        return False
    return VIEW_NAME in text and any(
        marker in text
        for marker in ("could not find", "does not exist", "schema cache")
    )


DEFAULT_LIMIT = 60
PAGE_SIZE = 24
# PostgREST отдаёт максимум 1000 строк за запрос; каталог тянет все строки
# для серверной сортировки, поэтому читаем страницами.
FETCH_BATCH = 1000
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
    - ranked (2+ оценок) — с рангом в топе
    - unknown (0-1 оценка) — между ranked, но не влияют на нумерацию топа
    """
    category_id = _category_filter(category_slug)
    if category_slug and category_id is None:
        return [], 0

    page = max(1, page)
    start = (page - 1) * limit

    def _apply_filters(request, *, view: bool):
        if not view:
            request = request.eq("status", "published")
        if category_id is not None:
            request = request.eq("category_id", category_id)
        if query:
            request = request.ilike("title", f"%{auth_service.ilike_literal(query)}%")
        return request

    def _fetch_all_rows(*, view: bool, expected: int) -> list[dict]:
        table = VIEW_NAME if view else "achievements"
        select = VIEW_SELECT if view else ACHIEVEMENT_SELECT
        rows: list[dict] = []
        while len(rows) < expected:
            batch = (
                _apply_filters(client.table(table).select(select), view=view)
                .range(len(rows), len(rows) + FETCH_BATCH - 1)
                .execute()
                .data
                or []
            )
            if not batch:
                break
            rows.extend(batch)
        return rows

    def _sort_rows(rows: list[dict]) -> list[dict]:
        if sort == SORT_NEW:
            # Сначала ranked/временные, затем предварительные; внутри групп —
            # новые сверху. Две стабильные сортировки сохраняют created_at.
            rows.sort(key=lambda a: a.get("created_at") or "", reverse=True)
            rows.sort(
                key=lambda a: (
                    not (a.get("ranking_status") == "ranked" or a.get("is_temporary"))
                )
            )
            return rows

        def sort_key(a):
            strength = a.get("sort_strength")
            average = a.get("average_position")
            rank_order = a.get("rank_order")
            return (
                strength is None,
                -float(strength) if strength is not None else 0.0,
                float(average) if average is not None else float("inf"),
                float(rank_order) if rank_order is not None else float("inf"),
                int(a["id"]),
            )

        rows.sort(key=sort_key)
        return rows

    client = auth_service.new_anon_client()
    try:
        count_res = (
            _apply_filters(
                client.table(VIEW_NAME).select("id", count="exact"), view=True
            )
            .limit(1)
            .execute()
        )
        total = count_res.count if count_res.count is not None else 0
        if start >= total:
            return [], total
        rows = _fetch_all_rows(view=True, expected=total)
        return _sort_rows(rows)[start : start + limit], total
    except APIError as exc:
        if not _is_missing_view(exc):
            raise

    count_res = (
        _apply_filters(
            client.table("achievements").select("id", count="exact"), view=False
        )
        .limit(1)
        .execute()
    )
    total = count_res.count if count_res.count is not None else 0
    if start >= total:
        return [], total

    rows = _fetch_all_rows(view=False, expected=total)
    # Прежний порядок: rank для ranked, average_position для предварительных.
    if sort == SORT_NEW:
        rows.sort(key=lambda a: a.get("created_at") or "", reverse=True)
        rows.sort(key=lambda a: a.get("ranking_status") != "ranked")
    else:

        def legacy_sort_key(a):
            value = a.get("rank") if a.get("ranking_status") == "ranked" else None
            if value is None:
                value = a.get("average_position")
            return (
                value is None,
                float(value) if value is not None else float("inf"),
                a.get("created_at") or "",
            )

        rows.sort(key=legacy_sort_key)
    return rows[start : start + limit], total


def _attach_view_fields(client, rows: list[dict]) -> None:
    """Подменяет постоянное место эффективным (с учётом временного топ-5).

    Вьюха содержит только published-строки; для остальных статусов поля
    остаются как в таблице. Если миграция 027 ещё не применена, тихо
    возвращаемся к постоянным значениям.
    """
    published_ids = [row["id"] for row in rows if row.get("status") == "published"]
    if not published_ids:
        return
    try:
        res = (
            client.table(VIEW_NAME)
            .select(",".join(("id", *VIEW_RANK_FIELDS)))
            .in_("id", published_ids)
            .execute()
        )
    except APIError as exc:
        if _is_missing_view(exc):
            return
        raise
    by_id = {row["id"]: row for row in res.data or []}
    for row in rows:
        view_row = by_id.get(row["id"])
        if view_row is None:
            continue
        for field in VIEW_RANK_FIELDS:
            if field in view_row:
                row[field] = view_row[field]
        row["is_temporary"] = bool(view_row.get("is_temporary"))


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
    item = res.data
    _attach_view_fields(client, [item])
    return item


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
    rows = res.data or []
    _attach_view_fields(client, rows)
    return rows


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
