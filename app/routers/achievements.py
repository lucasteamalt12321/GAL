import logging
import math
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import achievements as achievements_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service
from app.services import top5 as top5_service
from app.templating import templates

router = APIRouter(prefix="/achievements", tags=["achievements"])

logger = logging.getLogger(__name__)

LOGIN_URL = "/auth/login"

CREATE_TEMPLATE = "achievements/create.html"
EDIT_TEMPLATE = "achievements/edit.html"

LIST_INFO_MESSAGES = {
    "deleted": "Достижение удалено.",
}


def _parse_category_id(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw.isascii() or not raw.isdigit():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _form_context(
    *,
    error: str | None = None,
    title: str = "",
    description: str = "",
    requirements: str = "",
    category_id: str = "",
    external_url: str = "",
    achievement: dict | None = None,
    action: str = "/achievements/create",
    submit_label: str = "Отправить на модерацию",
    scale: list[dict] | None = None,
    harder_count: str = "",
    difficulty_label: str = "Ожидаемая сложность",
    blocked: bool = False,
    top5_claim_allowed: bool = False,
    top5_claim_checked: bool = False,
    top5_position: str = "",
    top5_justification: str = "",
    comment: str = "",
) -> dict:
    return {
        # Заблокированная форма категорий не показывает — не ходим в БД зря.
        "categories": [] if blocked else achievements_service.list_categories(),
        "error": error,
        "title": title,
        "description": description,
        "requirements": requirements,
        "category_id": category_id,
        "external_url": external_url,
        "achievement": achievement,
        "action": action,
        "submit_label": submit_label,
        "max_attachment_bytes": completions_service.MAX_ATTACHMENT_BYTES,
        "max_files_per_completion": completions_service.MAX_FILES_PER_COMPLETION,
        "max_comment_length": completions_service.MAX_COMMENT_LENGTH,
        "difficulty_scale": scale or [],
        "harder_count": harder_count,
        "difficulty_label": difficulty_label,
        "blocked": blocked,
        "top5_claim_allowed": top5_claim_allowed,
        "top5_claim_checked": top5_claim_checked,
        "top5_position": top5_position,
        "top5_justification": top5_justification,
        "comment": comment,
    }


def _create_context(**kwargs) -> dict:
    # В форме создания заявку в топ-5 подаёт автор: достижение ещё не
    # опубликовано, но заявка создаётся вместе с ним (миграция 034).
    kwargs.setdefault("top5_claim_allowed", True)
    return _form_context(**kwargs)


def _validate_fields(title: str, description: str, requirements: str) -> str | None:
    if not title:
        return "Название достижения обязательно."
    if len(title) > 200:
        return "Название слишком длинное (максимум 200 символов)."
    if len(description) > 4000:
        return "Описание слишком длинное (максимум 4000 символов)."
    if len(requirements) > 4000:
        return "Требования слишком длинные (максимум 4000 символов)."
    return None


def _rollback_created_achievement(request: Request, achievement_id: int) -> None:
    """Откатывает только что созданное достижение и его отпечаток.

    Отпечаток (реестр миграции 036) удаляется вместе со строкой: иначе повторная
    отправка того же текста блокировалась бы как «идентичная заявка», хотя
    заявки не было — достижение осталось бы неотправленным навсегда.
    """
    try:
        achievements_service.soft_delete_own_pending(request, achievement_id)
    except achievements_service.ACHIEVEMENT_ERRORS:
        logger.warning(
            "Failed to roll back achievement %s without proof",
            achievement_id,
            exc_info=True,
        )
    achievements_service.drop_submission_fingerprint(achievement_id)


def _create_submit_error(reason: str) -> str:
    """Текст ошибки отправки заявки для формы создания.

    Причина отказа сохраняется, но ограничивается по длине: сообщение из БД
    может быть техническим, а файл к формам прикладывать всё равно заново.
    """
    detail = (reason or "").strip()[:300]
    head = "Достижение не отправлено"
    if detail:
        head = f"{head}: {detail}"
    return f"{head}. Файл нужно приложить заново — отправьте форму ещё раз."


def _parse_top5_claim(
    top5_claim: str, top5_position: str, top5_justification: str
) -> tuple[int | None, str, str | None]:
    """Разбор полей заявки в топ-5: (место, обоснование, ошибка).

    Одинаково используется в форме создания достижения и в форме прохождения:
    экспертная оценка в заявке не участвует, решает модератор.
    """
    if top5_claim != "on":
        return None, "", None
    position = top5_service.parse_position(top5_position)
    justification = (top5_justification or "").strip()
    if position is None:
        return None, justification, "Укажите запрашиваемое место в топ-5 от 1 до 5."
    if not justification:
        return (
            None,
            justification,
            "Обоснование сложности обязательно для заявки в топ-5.",
        )
    if len(justification) > top5_service.MAX_JUSTIFICATION:
        return None, justification, "Обоснование сложности слишком длинное."
    return position, justification, None


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def list_page(
    request: Request,
    category: str | None = None,
    sort: str = achievements_service.SORT_RANK,
    q: str | None = None,
    page: int = 1,
    info: str | None = None,
) -> HTMLResponse:
    if sort not in achievements_service.ALLOWED_SORTS:
        sort = achievements_service.SORT_RANK
    query = (q or "").strip()[:100]
    page = max(1, page)
    categories = achievements_service.list_categories()
    items, total = achievements_service.list_achievements(
        category_slug=category, sort=sort, page=page, query=query or None
    )
    active = next((c for c in categories if c["slug"] == category), None)
    pages = max(1, math.ceil(total / achievements_service.PAGE_SIZE))
    if page > pages:
        redirect_url = request.url.include_query_params(page=pages)
        return RedirectResponse(
            f"{redirect_url.path}?{redirect_url.query}", status_code=303
        )
    return templates.TemplateResponse(
        request=request,
        name="achievements/list.html",
        context={
            "categories": categories,
            "achievements": items,
            "active_category": active,
            "category_missing": bool(category) and active is None,
            "sort": sort,
            "query": query,
            "page": page,
            "pages": pages,
            "total": total,
            "info": LIST_INFO_MESSAGES.get(info, info),
        },
    )


@router.get("/create", response_class=HTMLResponse, include_in_schema=False)
def create_page(request: Request) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    if not achievements_service.has_user_evaluation(request):
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error=achievements_service.CREATE_REQUIRES_EVALUATION,
                blocked=True,
            ),
        )
    return templates.TemplateResponse(
        request=request,
        name=CREATE_TEMPLATE,
        context=_create_context(scale=evaluation_service.list_user_scale(request)),
    )


@router.post("/create", response_class=HTMLResponse, include_in_schema=False)
def create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    requirements: str = Form(""),
    category_id: str = Form(""),
    external_url: str = Form(""),
    harder_count: str = Form(""),
    top5_claim: str = Form(""),
    top5_position: str = Form(""),
    top5_justification: str = Form(""),
    comment: str = Form(""),
    file: Annotated[UploadFile | None, File()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)

    if not achievements_service.has_user_evaluation(request):
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error=achievements_service.CREATE_REQUIRES_EVALUATION,
                blocked=True,
            ),
        )

    title = title.strip()
    description = description.strip()
    requirements = requirements.strip()
    external_url = external_url.strip()
    comment = comment.strip()
    uploads = completions_service.collect_uploads(files, file)
    has_file = bool(uploads)
    parsed_category = _parse_category_id(category_id)
    scale = evaluation_service.list_user_scale(request)
    parsed_harder = evaluation_service.parse_harder_count(harder_count, len(scale))

    claim_position, claim_justification, claim_error = _parse_top5_claim(
        top5_claim, top5_position, top5_justification
    )
    # Поля заявки сохраняем при перерисовке формы — иначе пользователь теряет
    # отметку, обоснование и комментарий после любой ошибки.
    claim_state = {
        "top5_claim_checked": top5_claim == "on",
        "top5_position": top5_position,
        "top5_justification": claim_justification,
        "comment": comment,
    }

    field_error = _validate_fields(title, description, requirements)
    if field_error:
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error=field_error,
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
                scale=scale,
                harder_count=harder_count,
                **claim_state,
            ),
        )

    if parsed_harder is None:
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error="Укажите, где достижение стоит в вашей шкале сложности.",
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
                scale=scale,
                harder_count=harder_count,
                **claim_state,
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
                scale=scale,
                harder_count=harder_count,
                **claim_state,
            ),
        )

    if claim_error is not None:
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error=claim_error,
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
                scale=scale,
                harder_count=harder_count,
                **claim_state,
            ),
        )

    # create и submit_completion разведены по разным except: общий блок ловил
    # ошибку второго вызова и перерисовывал форму create, хотя строка в
    # achievements уже была вставлена -> повторный сабмит давал дубль
    # достижения без доказательства в очереди модерации.
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
            name=CREATE_TEMPLATE,
            context=_create_context(
                error=achievements_service.friendly_achievement_error(
                    exc,
                    default="Не удалось создать достижение. Попробуйте ещё раз.",
                ),
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
                scale=scale,
                harder_count=harder_count,
                **claim_state,
            ),
        )

    try:
        completion = completions_service.submit_completion(
            request,
            achievement_id=item["id"],
            description="",
            comment=(comment or "").strip(),
            external_url=external_url,
            uploads=uploads,
            harder_count=parsed_harder,
            proofs_visible=True,
        )
    except completions_service.CompletionError as exc:
        _rollback_created_achievement(request, item["id"])
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error=_create_submit_error(str(exc)),
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
                scale=scale,
                harder_count=harder_count,
                **claim_state,
            ),
        )
    except completions_service.COMPLETION_ERRORS:
        _rollback_created_achievement(request, item["id"])
        return templates.TemplateResponse(
            request=request,
            name=CREATE_TEMPLATE,
            context=_create_context(
                error=(
                    "Достижение не сохранено: доказательство не прикрепилось. "
                    "Приложите файл или ссылку заново и отправьте форму ещё раз."
                ),
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                external_url=external_url,
                scale=scale,
                harder_count=harder_count,
                **claim_state,
            ),
        )

    if claim_position is not None:
        # Заявка в топ-5 создаётся вместе с достижением: доказательство заявки —
        # собственное прохождение автора (миграция 034).
        try:
            top5_service.submit_application(
                request,
                item["id"],
                proposed_position=claim_position,
                justification=claim_justification,
                completion_id=int(completion["id"]),
            )
        except top5_service.Top5Error as exc:
            return RedirectResponse(
                f"/achievements/{item['id']}?info=submitted"
                f"&error={quote(f'Заявка в топ-5 не создана: {exc}')}",
                status_code=303,
            )
        except top5_service.TOP5_ERRORS:
            return RedirectResponse(
                f"/achievements/{item['id']}?info=submitted"
                f"&error={quote('Заявка в топ-5 не создана. Попробуйте ещё раз.')}",
                status_code=303,
            )
        return RedirectResponse(
            f"/achievements/{item['id']}?info=submitted_top5", status_code=303
        )
    return RedirectResponse(
        f"/achievements/{item['id']}?info=submitted", status_code=303
    )


@router.get(
    "/{achievement_id}/edit", response_class=HTMLResponse, include_in_schema=False
)
def edit_page(request: Request, achievement_id: int) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    item = achievements_service.get_achievement(request, achievement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    _require_own_pending(request, item)
    return templates.TemplateResponse(
        request=request,
        name=EDIT_TEMPLATE,
        context=_form_context(
            title=item["title"],
            description=item.get("description") or "",
            requirements=item.get("requirements") or "",
            category_id=str(item.get("category_id") or ""),
            achievement=item,
            action=f"/achievements/{achievement_id}/edit",
            submit_label="Сохранить изменения",
        ),
    )


@router.post(
    "/{achievement_id}/edit", response_class=HTMLResponse, include_in_schema=False
)
def edit(
    request: Request,
    achievement_id: int,
    title: str = Form(...),
    description: str = Form(""),
    requirements: str = Form(""),
    category_id: str = Form(""),
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    item = achievements_service.get_achievement(request, achievement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    _require_own_pending(request, item)

    title = title.strip()
    description = description.strip()
    requirements = requirements.strip()
    field_error = _validate_fields(title, description, requirements)
    if field_error:
        return templates.TemplateResponse(
            request=request,
            name=EDIT_TEMPLATE,
            context=_form_context(
                error=field_error,
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                achievement=item,
                action=f"/achievements/{achievement_id}/edit",
                submit_label="Сохранить изменения",
            ),
        )
    try:
        achievements_service.update_achievement(
            request,
            achievement_id,
            title=title,
            description=description,
            requirements=requirements,
            category_id=_parse_category_id(category_id),
        )
    except achievements_service.ACHIEVEMENT_ERRORS as exc:
        return templates.TemplateResponse(
            request=request,
            name=EDIT_TEMPLATE,
            context=_form_context(
                error=achievements_service.friendly_achievement_error(
                    exc, default="Не удалось сохранить изменения."
                ),
                title=title,
                description=description,
                requirements=requirements,
                category_id=category_id,
                achievement=item,
                action=f"/achievements/{achievement_id}/edit",
                submit_label="Сохранить изменения",
            ),
        )
    return RedirectResponse(
        f"/achievements/{achievement_id}?info=updated", status_code=303
    )


@router.post(
    "/{achievement_id}/complete",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def complete(
    request: Request,
    achievement_id: int,
    description: str = Form(""),
    external_url: str = Form(""),
    harder_count: str = Form(""),
    hide_proofs: str = Form(""),
    top5_claim: str = Form(""),
    top5_position: str = Form(""),
    top5_justification: str = Form(""),
    comment: str = Form(""),
    file: Annotated[UploadFile | None, File()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    achievement = achievements_service.get_achievement(request, achievement_id)
    if achievement is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    is_creator = str(request.state.user.id) == str(achievement["creator_id"])
    allowed = achievement["status"] == "published" or (
        achievement["status"] == "pending" and is_creator
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="Achievement not found")
    scale = evaluation_service.list_user_scale(request)
    others = [row for row in scale if row["achievement_id"] != achievement_id]
    parsed = evaluation_service.parse_harder_count(harder_count, len(others))
    if parsed is None:
        return RedirectResponse(
            f"/achievements/{achievement_id}"
            f"?error={quote('Выберите, сложнее или легче ваших достижений новое.')}",
            status_code=303,
        )
    claim_position, claim_justification, claim_error = _parse_top5_claim(
        top5_claim, top5_position, top5_justification
    )
    if claim_error is not None:
        return RedirectResponse(
            f"/achievements/{achievement_id}?error={quote(claim_error)}",
            status_code=303,
        )
    try:
        completion = completions_service.submit_completion(
            request,
            achievement_id=achievement_id,
            # Описание отдельного доказательства не заполняется: комментарий
            # относится ко всей заявке и хранится в achievement_completions.
            description="",
            # Комментарий относится к самому прохождению (документу), а не к
            # отдельному файлу: он хранится в achievement_completions.comment
            # и виден там же, где видны доказательства.
            comment=(comment or description).strip(),
            external_url=external_url,
            uploads=completions_service.collect_uploads(files, file),
            harder_count=parsed,
            # Верификация создателя всегда публична; обычный пользователь
            # может скрыть доказательство от остальных.
            proofs_visible=is_creator or hide_proofs != "on",
        )
    except completions_service.CompletionError as exc:
        return RedirectResponse(
            f"/achievements/{achievement_id}?error={quote(str(exc))}", status_code=303
        )
    if claim_position is not None:
        try:
            top5_service.submit_application(
                request,
                achievement_id,
                proposed_position=claim_position,
                justification=claim_justification,
                completion_id=int(completion["id"]),
            )
        except top5_service.Top5Error as exc:
            return RedirectResponse(
                f"/achievements/{achievement_id}?info=submitted"
                f"&error={quote(f'Заявка в топ-5 не создана: {exc}')}",
                status_code=303,
            )
        except top5_service.TOP5_ERRORS:
            return RedirectResponse(
                f"/achievements/{achievement_id}?info=submitted"
                f"&error={quote('Заявка в топ-5 не создана. Попробуйте ещё раз.')}",
                status_code=303,
            )
        return RedirectResponse(
            f"/achievements/{achievement_id}?info=submitted_top5", status_code=303
        )
    return RedirectResponse(
        f"/achievements/{achievement_id}?info=submitted", status_code=303
    )


@router.post(
    "/{achievement_id}/delete",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def delete(request: Request, achievement_id: int) -> HTMLResponse:
    if request.state.user is None:
        return RedirectResponse(LOGIN_URL, status_code=303)
    item = achievements_service.get_achievement(request, achievement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Achievement not found")
    _require_own_pending(request, item)
    try:
        achievements_service.soft_delete_own_pending(request, achievement_id)
    except achievements_service.ACHIEVEMENT_ERRORS as exc:
        return RedirectResponse(
            f"/achievements/{achievement_id}?error={quote(str(exc)[:300])}",
            status_code=303,
        )
    username = getattr(request.state.user, "username", None)
    if username:
        return RedirectResponse(f"/users/{username}?info=deleted", status_code=303)
    return RedirectResponse("/achievements?info=deleted", status_code=303)


def _require_own_pending(request: Request, item: dict) -> None:
    user = request.state.user
    is_creator = user is not None and str(user.id) == str(item.get("creator_id"))
    if not is_creator or item.get("status") != "pending":
        raise HTTPException(status_code=403, detail="Achievement is not editable")


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
    is_moderator = user is not None and user.role in ("moderator", "admin")

    own_completion = None
    proof_views: list[dict] = []
    my_evaluation = None
    can_evaluate = False
    eval_reason: str | None = None
    if user is not None:
        own_completion = completions_service.get_user_completion(
            request, achievement_id
        )
        if own_completion is not None:
            proofs = completions_service.list_proofs(request, own_completion["id"])
            if proofs:
                proof_views = completions_service.decorate_proofs(request, proofs)
        can_evaluate, eval_reason, my_evaluation = evaluation_service.evaluate_context(
            request, item
        )

    can_submit = (
        user is not None
        and own_completion is None
        and (
            item["status"] == "published"
            or (is_creator and item["status"] == "pending")
        )
    )
    info_messages = {
        "submitted": "Доказательство отправлено на модерацию.",
        "submitted_top5": "Доказательство и заявка в топ-5 отправлены на модерацию.",
        "evaluated": "Оценка сохранена.",
        "updated": "Изменения сохранены и отправлены на модерацию.",
        "report-submitted": "Жалоба отправлена. Модератор её рассмотрит.",
        "hidden": "Достижение скрыто из каталога и рейтинга.",
    }
    rejection_reason = None
    if own_completion is not None and own_completion.get("status") == "rejected":
        rejection_reason = completions_service.get_rejection_reason(
            request, own_completion["id"]
        )
        can_submit = True

    difficulty_scale: list[dict] = []
    if user is not None:
        difficulty_scale = [
            row
            for row in evaluation_service.list_user_scale(request)
            if row["achievement_id"] != achievement_id
        ]
    difficulty_label = (
        "Ожидаемая сложность"
        if is_creator and item["status"] == "pending"
        else "Сложность"
    )
    existing_harder_count = ""
    if own_completion is not None:
        value = own_completion.get("requested_harder_count")
        if value is not None:
            existing_harder_count = str(value)
    completers = (
        completions_service.list_achievement_completers(request, achievement_id)
        if item["status"] == "published"
        else []
    )
    top5_context = top5_service.application_context(request, item)
    return templates.TemplateResponse(
        request=request,
        name="achievements/detail.html",
        context={
            "achievement": item,
            "completers": completers,
            "is_creator": is_creator,
            "is_moderator": is_moderator,
            "can_edit": is_creator and item["status"] == "pending",
            "can_hide": is_moderator and item["status"] == "published",
            "own_completion": own_completion,
            "proofs": proof_views,
            "can_submit": can_submit,
            "rejection_reason": rejection_reason,
            "difficulty_scale": difficulty_scale,
            "difficulty_label": difficulty_label,
            "harder_count": existing_harder_count,
            "my_evaluation": my_evaluation,
            "can_evaluate": can_evaluate,
            "eval_reason": eval_reason,
            "max_attachment_bytes": completions_service.MAX_ATTACHMENT_BYTES,
            "max_files_per_completion": completions_service.MAX_FILES_PER_COMPLETION,
            "max_comment_length": completions_service.MAX_COMMENT_LENGTH,
            "top5_context": top5_context,
            "top5_claim_allowed": can_submit and top5_context["can_claim"],
            "error": error,
            "info": info_messages.get(info, info),
        },
    )
