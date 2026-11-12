"""GAL leaderboards data access.

Achievement leaderboard читает опубликованные ranked-достижения напрямую
(RLS разрешает anon SELECT на published). Player leaderboard использует
security definer функцию public.player_leaderboard_page() (агрегат по approved
completions, недоступным публично под RLS).
"""

from __future__ import annotations

import logging

from postgrest.exceptions import APIError

from app.services import auth as auth_service

logger = logging.getLogger(__name__)

LEGACY_RANKED_SELECT = (
    "id,title,rank,ranking_status,average_position,evaluation_count,"
    "bt_strength,bt_rating,bt_comparisons,"
    "category:categories(id,name,slug)"
)
RANKED_SELECT = (
    "id,title,rank,ranking_status,average_position,evaluation_count,"
    "bt_strength,bt_rating,bt_comparisons,"
    "bt_rating_se,bt_rating_ci_low,bt_rating_ci_high,"
    "category:categories(id,name,slug)"
)
UNCERTAINTY_FIELDS = ("bt_rating_se", "bt_rating_ci_low", "bt_rating_ci_high")
PAGE_SIZE = 25


def _is_missing_uncertainty_columns(exc: APIError) -> bool:
    """Миграция 025 может быть ещё не применена к БД.

    Новый select тогда падает на неизвестных колонках; это не сбой хранилища,
    поэтому лидерборд откатывается к прежнему набору полей.
    """
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'details', '')}".lower()
    return any(field in text for field in UNCERTAINTY_FIELDS)


def _ranked_page(client, select: str, start: int, limit: int):
    return (
        client.table("achievements")
        .select(select)
        .eq("status", "published")
        .eq("ranking_status", "ranked")
        .order("rank")
        .range(start, start + limit - 1)
        .execute()
    )


def leaderboard_achievements(
    *, page: int = 1, limit: int = PAGE_SIZE
) -> tuple[list[dict], int]:
    """Возвращает (страница достижений, всего в рейтинге).

    Count запрашивается отдельно: range за пределами выборки даёт 416,
    который глобальный обработчик принял бы за сбой Supabase.
    """
    client = auth_service.new_anon_client()
    count_res = (
        client.table("achievements")
        .select("id", count="exact")
        .eq("status", "published")
        .eq("ranking_status", "ranked")
        .limit(1)
        .execute()
    )
    total = count_res.count if count_res.count is not None else 0
    page = max(1, page)
    start = (page - 1) * limit
    if start >= total:
        return [], total
    try:
        res = _ranked_page(client, RANKED_SELECT, start, limit)
    except APIError as exc:
        if not _is_missing_uncertainty_columns(exc):
            raise
        logger.warning(
            "BT uncertainty columns are missing in Supabase; using legacy select"
        )
        res = _ranked_page(client, LEGACY_RANKED_SELECT, start, limit)
    return res.data or [], total


def player_leaderboard(
    *, page: int = 1, limit: int = PAGE_SIZE
) -> tuple[list[dict], int]:
    """Возвращает (страница игроков, всего игроков).

    На БД с миграцией 013 доступна постраничная RPC; до её применения
    используем старую функцию и режем результат на сервере.
    """
    client = auth_service.new_anon_client()
    page = max(1, page)
    offset = (page - 1) * limit
    try:
        res = client.rpc(
            "player_leaderboard_page",
            {"p_limit": limit, "p_offset": offset},
        ).execute()
    except APIError:
        res = client.rpc("player_leaderboard").execute()
        rows = res.data or []
        total = len(rows)
        return rows[offset : offset + limit], total
    rows = res.data or []
    total = int(rows[0].get("total_count") or 0) if rows else 0
    return rows, total
