"""GAL leaderboards data access.

Achievement leaderboard читает вьюху ``public.public_achievements``: она
объединяет постоянный рейтинг и временные места топ-5, поэтому номера
(#N / #N*) и порядок совпадают с каталогом и профилем. До применения
миграции 027 вьюхи нет — тогда используется прежний запрос к ``achievements``.

Player leaderboard использует security definer функцию
public.player_leaderboard_search_page() (агрегат по approved completions,
недоступным публично под RLS). До миграции 040 поиск и пагинация выполняются
в Python поверх legacy RPC player_leaderboard().
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
# Достижение с одной оценкой уже имеет предварительную силу BT и показывается
# в рейтинге, но без номера, пока не наберёт вторую оценку.
MIN_EVALUATIONS = 1

VIEW_NAME = "public_achievements"
VIEW_SELECT = (
    "id,title,rank,is_temporary,evaluation_count,bt_rating,bt_comparisons,"
    "bt_rating_se,bt_rating_ci_low,bt_rating_ci_high,category"
)


def _error_text(exc: APIError) -> str:
    return f"{getattr(exc, 'message', '')} {getattr(exc, 'details', '')}".lower()


def _is_missing_uncertainty_columns(exc: APIError) -> bool:
    """Миграция 025 может быть ещё не применена к БД.

    Новый select тогда падает на неизвестных колонках; это не сбой хранилища,
    поэтому лидерборд откатывается к прежнему набору полей.
    """
    text = _error_text(exc)
    return any(field in text for field in UNCERTAINTY_FIELDS)


def is_missing_public_view(exc: APIError) -> bool:
    """Вьюха public_achievements появляется только в миграции 027.

    Permission denied тоже упоминает имя вьюхи, поэтому проверяем маркеры
    отсутствующего отношения, а не только имя. Ошибка неизвестной колонки
    (PGRST204) означает, что вьюха есть, и откат к таблице не нужен.
    """
    code = str(getattr(exc, "code", "") or "").upper()
    text = _error_text(exc)
    if code == "PGRST205" or "pgrst205" in text:
        return True
    if code == "PGRST204" or "column" in text:
        return False
    return VIEW_NAME in text and any(
        marker in text
        for marker in ("could not find", "does not exist", "schema cache")
    )


def _is_missing_function(exc: APIError) -> bool:
    """RPC поиска игроков появляется только в миграции 040."""
    code = str(getattr(exc, "code", "") or "").upper()
    text = _error_text(exc)
    if code == "PGRST202" or "pgrst202" in text:
        return True
    return "could not find the function" in text


def _view_page(client, start: int, limit: int):
    return (
        client.table(VIEW_NAME)
        .select(VIEW_SELECT)
        .eq("in_leaderboard", True)
        .order("sort_strength", desc=True, nullsfirst=False)
        .order("average_position", nullsfirst=False)
        .order("rank", nullsfirst=False)
        .order("id")
        .range(start, start + limit - 1)
        .execute()
    )


def _table_page(client, select: str, start: int, limit: int):
    return (
        client.table("achievements")
        .select(select)
        .eq("status", "published")
        .gte("evaluation_count", MIN_EVALUATIONS)
        .order("bt_strength", desc=True, nullsfirst=False)
        .order("average_position", nullsfirst=False)
        .order("rank", nullsfirst=False)
        .order("id")
        .range(start, start + limit - 1)
        .execute()
    )


def leaderboard_achievements(
    *, page: int = 1, limit: int = PAGE_SIZE
) -> tuple[list[dict], int]:
    """Возвращает (страница достижений, всего оценённых).

    Порядок задаёт вьюха: sort_strength (временная сила топ-5 или постоянная
    BT), затем average_position/rank/id. Временные строки получают номер со
    звёздочкой и сдвигают постоянные, предварительные строки номера не имеют.

    Count запрашивается отдельно: range за пределами выборки даёт 416,
    который глобальный обработчик принял бы за сбой Supabase.
    """
    client = auth_service.new_anon_client()
    page = max(1, page)
    start = (page - 1) * limit
    try:
        count_res = (
            client.table(VIEW_NAME)
            .select("id", count="exact")
            .eq("in_leaderboard", True)
            .limit(1)
            .execute()
        )
        total = count_res.count if count_res.count is not None else 0
        if start >= total:
            return [], total
        res = _view_page(client, start, limit)
        return res.data or [], total
    except APIError as exc:
        if not is_missing_public_view(exc):
            raise
        logger.warning(
            "public_achievements view is missing in Supabase; using legacy table"
        )

    count_res = (
        client.table("achievements")
        .select("id", count="exact")
        .eq("status", "published")
        .gte("evaluation_count", MIN_EVALUATIONS)
        .limit(1)
        .execute()
    )
    total = count_res.count if count_res.count is not None else 0
    if start >= total:
        return [], total
    try:
        res = _table_page(client, RANKED_SELECT, start, limit)
    except APIError as exc:
        if not _is_missing_uncertainty_columns(exc):
            raise
        logger.warning(
            "BT uncertainty columns are missing in Supabase; using legacy select"
        )
        res = _table_page(client, LEGACY_RANKED_SELECT, start, limit)
    return res.data or [], total


def max_ranked_bt_rating() -> float | None:
    """Максимальный BT-рейтинг среди достижений, дающих очки."""
    client = auth_service.new_anon_client()
    res = (
        client.table("achievements")
        .select("bt_rating")
        .eq("status", "published")
        .eq("ranking_status", "ranked")
        .not_.is_("rank", None)
        .not_.is_("bt_rating", None)
        .order("bt_rating", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return float(res.data[0]["bt_rating"])


def player_leaderboard(
    *, page: int = 1, limit: int = PAGE_SIZE, query: str | None = None
) -> tuple[list[dict], int]:
    """Возвращает (страница игроков, всего найденных игроков)."""
    client = auth_service.new_anon_client()
    page = max(1, page)
    offset = (page - 1) * limit
    search = (query or "").strip()[:100]
    params = {"p_limit": limit, "p_offset": offset, "p_query": search}
    try:
        res = client.rpc("player_leaderboard_search_page", params).execute()
    except APIError as exc:
        if not _is_missing_function(exc):
            raise
        res = client.rpc("player_leaderboard").execute()
        rows = res.data or []
        if search:
            needle = search.casefold()
            rows = [
                row
                for row in rows
                if needle in (row.get("username") or "").casefold()
                or needle in (row.get("display_name") or "").casefold()
            ]
        rows.sort(
            key=lambda row: (
                -float(row.get("score") or 0),
                (row.get("username") or "").casefold(),
            )
        )
        total = len(rows)
        return rows[offset : offset + limit], total
    rows = res.data or []
    # total_count приходит только со строкой: за пределами выборки нужен
    # отдельный запрос, иначе несуществующая страница сбросится до первой.
    if not rows and offset:
        count_res = client.rpc(
            "player_leaderboard_search_page",
            {**params, "p_limit": 1, "p_offset": 0},
        ).execute()
        count_rows = count_res.data or []
        total = int(count_rows[0].get("total_count") or 0) if count_rows else 0
    else:
        total = int(rows[0].get("total_count") or 0) if rows else 0
    return rows, total
