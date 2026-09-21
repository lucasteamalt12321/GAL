from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import achievements as achievements_service
from app.templating import templates

router = APIRouter(prefix="/achievements", tags=["achievements"])

LOGIN_URL = "/auth/login"


def _parse_category_id(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def list_page(
    request: Request,
    category: str | None = None,
    sort: str = achievements_service.SORT_RANK,
) -> HTMLResponse:
    if sort not in achievements_service.ALLOWED_SORTS:
        sort = achievements_service.SORT_RANK
    categories = achievements_service.list_categories()
    items = achievements_service.list_achievements(category_slug=category, sort=sort)
    active = next((c for c in categories if c["slug"] == category), None)
    return templates.TemplateResponse(
        request=request,
        name="achievements/list.html",
        context={
            "categories": categories,
            "achievements": items,
            "active_category": active,
            "sort": sort,
        },
    )


@router.get("/create", response_class=HTMLResponse, include_in_schema=False)
def create_page(request: Request) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="achievements/create.html",
        context={
            "categories": achievements_service.list_categories(),
            "error": None,
            "title": "",
            "description": "",
            "requirements": "",
            "category_id": "",
        },
    )


@router.post("/create", response_class=HTMLResponse, include_in_schema=False)
def create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    requirements: str = Form(""),
    category_id: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)

    title = title.strip()
    description = description.strip()
    requirements = requirements.strip()
    parsed_category = _parse_category_id(category_id)

    if not title:
        return templates.TemplateResponse(
            request=request,
            name="achievements/create.html",
            context={
                "categories": achievements_service.list_categories(),
                "error": "Название достижения обязательно.",
                "title": title,
                "description": description,
                "requirements": requirements,
                "category_id": category_id,
            },
        )

    try:
        item = achievements_service.create_achievement(
            request,
            title=title,
            description=description,
            requirements=requirements,
            category_id=parsed_category,
        )
    except achievements_service.ACHIEVEMENT_ERRORS as exc:
        return templates.TemplateResponse(
            request=request,
            name="achievements/create.html",
            context={
                "categories": achievements_service.list_categories(),
                "error": str(exc)[:300]
                or "Не удалось создать достижение. Попробуйте ещё раз.",
                "title": title,
                "description": description,
                "requirements": requirements,
                "category_id": category_id,
            },
        )
    return RedirectResponse(f"/achievements/{item['id']}", status_code=303)


@router.get("/{achievement_id}", response_class=HTMLResponse, include_in_schema=False)
def detail(request: Request, achievement_id: int) -> HTMLResponse:
    item = achievements_service.get_achievement(request, achievement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    user = request.state.user
    is_creator = user is not None and str(user.id) == str(item["creator_id"])
    return templates.TemplateResponse(
        request=request,
        name="achievements/detail.html",
        context={"achievement": item, "is_creator": is_creator},
    )
