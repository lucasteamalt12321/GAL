from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import achievements as achievements_service
from app.services import completions as completions_service
from app.templating import templates

router = APIRouter(prefix="/achievements", tags=["achievements"])

LOGIN_URL = "/auth/login"

CREATE_TEMPLATE = "achievements/create.html"


def _parse_category_id(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def _create_context(
    *,
    error: str | None = None,
    title: str = "",
    description: str = "",
    requirements: str = "",
    category_id: str = "",
    external_url: str = "",
) -> dict:
    return {
        "categories": achievements_service.list_categories(),
        "error": error,
        "title": title,
        "description": description,
        "requirements": requirements,
        "category_id": category_id,
        "external_url": external_url,
    }


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
        name=CREATE_TEMPLATE,
        context=_create_context(),
    )


@router.post("/create", response_class=HTMLResponse, include_in_schema=False)
def create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    requirements: str = Form(""),
    category_id: str = Form(""),
    external_url: str = Form(""),
    file: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)

    title = title.strip()
    description = description.strip()
    requirements = requirements.strip()
    external_url = external_url.strip()
    has_file = bool(file is not None and file.filename)
    parsed_category = _parse_category_id(category_id)

    if not title:
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error="Название достижения обязательно.",
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
            ),
        )

    if not external_url and not has_file:
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error="Приложите доказательство своего выполнения: файл или ссылку.",
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
            ),
        )

    try:
        item = achievements_service.create_achievement(
            request,
            title=title,
            description=description,
            requirements=requirements,
            category_id=parsed_category,
        )
        completions_service.submit_completion(
            request,
            achievement_id=item["id"],
            description="Доказательство создателя",
            external_url=external_url,
            uploaded=file,
        )
    except completions_service.CompletionError as exc:
        return RedirectResponse(
            f"/achievements/{item['id']}?error={quote(str(exc))}", status_code=303
        )
    except achievements_service.ACHIEVEMENT_ERRORS as exc:
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error=str(exc)[:300]
                or "Не удалось создать достижение. Попробуйте ещё раз.",
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
            ),
        )
    return RedirectResponse(
        f"/achievements/{item['id']}?info=submitted", status_code=303
    )


@router.get("/{achievement_id}", response_class=HTMLResponse, include_in_schema=False)
def detail(
    request: Request,
    achievement_id: int,
    error: str | None = None,
    info: str | None = None,
) -> HTMLResponse:
    item = achievements_service.get_achievement(request, achievement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    user = request.state.user
    is_creator = user is not None and str(user.id) == str(item["creator_id"])

    own_completion = None
    proof_views: list[dict] = []
    if user is not None:
        own_completion = completions_service.get_user_completion(
            request, achievement_id
        )
        if own_completion is not None:
            proofs = completions_service.list_proofs(request, own_completion["id"])
            proof_views = completions_service.decorate_proofs(request, proofs)

    can_submit = (
        user is not None and own_completion is None and item["status"] == "published"
    )
    info_messages = {"submitted": "Доказательство отправлено на модерацию."}
    return templates.TemplateResponse(
        request=request,
        name="achievements/detail.html",
        context={
            "achievement": item,
            "is_creator": is_creator,
            "own_completion": own_completion,
            "proofs": proof_views,
            "can_submit": can_submit,
            "error": error,
            "info": info_messages.get(info, info),
        },
    )
