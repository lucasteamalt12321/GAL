"""GAL leaderboards: achievement rank list + player scores."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services import rankings as rankings_service
from app.templating import templates

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


def _enrich_players(players: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, row in enumerate(players, start=1):
        entry = dict(row)
        try:
            entry["score"] = round(float(row["score"]), 2)
        except (TypeError, ValueError, KeyError):
            entry["score"] = 0.0
        entry["leaderboard_position"] = i
        out.append(entry)
    return out


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def leaderboard_page(request: Request) -> HTMLResponse:
    achievements = rankings_service.leaderboard_achievements()
    players = _enrich_players(rankings_service.player_leaderboard())
    return templates.TemplateResponse(
        request=request,
        name="rankings/leaderboard.html",
        context={
            "achievements": achievements,
            "players": players,
        },
    )
