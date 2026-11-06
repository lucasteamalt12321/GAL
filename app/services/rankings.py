"""GAL leaderboards data access.

Achievement leaderboard читает опубликованные ranked-достижения напрямую
(RLS разрешает anon SELECT на published). Player leaderboard использует
security definer функцию public.player_leaderboard_page() (агрегат по approved
completions, недоступным публично под RLS).
"""

from __future__ import annotations

from postgrest.exceptions import APIError

from app.services import auth as auth_service

RANKED_SELECT = (
    "id,title,rank,ranking_status,average_position,evaluation_count,"
    "bt_strength,bt_rating,bt_comparisons,"
    "category:categories(id,name,slug)"
)
PAGE_SIZE = 25


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
    res = (
        client.table("achievements")
        .select(RANKED_SELECT)
        .eq("status", "published")
        .eq("ranking_status", "ranked")
        .order("rank")
        .range(start, start + limit - 1)
        .execute()
    )
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
