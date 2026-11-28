"""GAL player leaderboard."""

import math

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
    players_page: int = 1,
    q: str | None = None,
) -> HTMLResponse:
    players_page = max(1, players_page)
    query = (q or "").strip()[:100]
    players, total = rankings_service.player_leaderboard(
        page=players_page, query=query or None
    )
    players = _enrich_players(players)
    pages = max(1, math.ceil(total / rankings_service.PAGE_SIZE))

    if players_page > pages:
        redirect_url = request.url.include_query_params(players_page=pages)
        return RedirectResponse(
            f"{redirect_url.path}?{redirect_url.query}", status_code=303
        )

    return templates.TemplateResponse(
        request=request,
        name="rankings/leaderboard.html",
        context={
            "players": players,
            "page": players_page,
            "pages": pages,
            "total": total,
            "query": query,
            "page_size": rankings_service.PAGE_SIZE,
        },
    )
