"""GAL leaderboards data access.

Achievement leaderboard читает опубликованные ranked-достижения напрямую
(RLS разрешает anon SELECT на published). Player leaderboard использует
security definer функцию public.player_leaderboard() (агрегат по approved
completions, недоступным публично под RLS).
"""

from __future__ import annotations

from app.services import auth as auth_service

RANKED_SELECT = (
    "id,title,rank,ranking_status,average_position,evaluation_count,"
    "category:categories(id,name,slug)"
)
DEFAULT_LIMIT = 100


def leaderboard_achievements(limit: int = DEFAULT_LIMIT) -> list[dict]:
    client = auth_service.new_anon_client()
    res = (
        client.table("achievements")
        .select(RANKED_SELECT)
        .eq("status", "published")
        .eq("ranking_status", "ranked")
        .order("rank")
        .limit(limit)
        .execute()
    )
    return res.data or []


def player_leaderboard() -> list[dict]:
    client = auth_service.new_anon_client()
    res = client.rpc("player_leaderboard").execute()
    return res.data or []
