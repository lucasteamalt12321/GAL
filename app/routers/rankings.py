"""GAL leaderboards: achievement rank list + player scores."""

import math
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import rankings as rankings_service
from app.templating import templates

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


def _enrich_players(players: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in players:
        entry = dict(row)
        try:
            entry["score"] = round(float(row["score"]), 2)
        except (TypeError, ValueError, KeyError):
            entry["score"] = 0.0
        out.append(entry)
    return out


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def leaderboard_page(
    request: Request,
    page: int = 1,
    players_page: int = 1,
) -> HTMLResponse:
    page = max(1, page)
    players_page = max(1, players_page)
    # Два независимых чтения грузим параллельно: так суммарное время страницы
    # ограничено одним таймаутом Supabase, а не двумя подряд.
    with ThreadPoolExecutor(max_workers=2) as pool:
        achievements_future = pool.submit(
            rankings_service.leaderboard_achievements, page=page
        )
        players_future = pool.submit(
            rankings_service.player_leaderboard, page=players_page
        )
        achievements, achievements_total = achievements_future.result()
        players, players_total = players_future.result()
    players = _enrich_players(players)

    achievements_pages = max(
        1, math.ceil(achievements_total / rankings_service.PAGE_SIZE)
    )
    players_pages = max(1, math.ceil(players_total / rankings_service.PAGE_SIZE))

    if page > achievements_pages:
        redirect_url = request.url.include_query_params(page=achievements_pages)
        return RedirectResponse(
            f"{redirect_url.path}?{redirect_url.query}", status_code=303
        )
    if players_page > players_pages:
        redirect_url = request.url.include_query_params(players_page=players_pages)
        return RedirectResponse(
            f"{redirect_url.path}?{redirect_url.query}", status_code=303
        )

    return templates.TemplateResponse(
        request=request,
        name="rankings/leaderboard.html",
        context={
            "achievements": achievements,
            "players": players,
            "page": page,
            "pages": achievements_pages,
            "total": achievements_total,
            "players_page": players_page,
            "players_pages": players_pages,
            "players_total": players_total,
        },
    )
