from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import top5 as top5_service
from app.templating import templates

router = APIRouter(prefix="/top5", tags=["top5"])

LOGIN_URL = "/auth/login"
MY_URL = "/submissions"

INFO_MESSAGES = {
    "decided": "Решение сохранено.",
}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _error_url(url: str, message: str) -> str:
    return f"{url}?error={quote(message)}"


@router.get("", include_in_schema=False)
def my_page(request: Request) -> RedirectResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    return _redirect(MY_URL)


@router.post(
    "/{application_id}/cancel",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def cancel(request: Request, application_id: int) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    try:
        top5_service.cancel_application(request, application_id)
    except top5_service.Top5Error as exc:
        return _redirect(_error_url(MY_URL, str(exc)))
    except top5_service.TOP5_ERRORS:
        return _redirect(
            _error_url(MY_URL, "Не удалось отозвать заявку. Попробуйте ещё раз.")
        )
    return _redirect(f"{MY_URL}?info=cancelled")


@router.get("/{application_id}", response_class=HTMLResponse, include_in_schema=False)
def detail(
    request: Request,
    application_id: int,
    info: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if request.state.user is None:
        return _redirect(LOGIN_URL)
    bundle = top5_service.application_bundle(request, application_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return templates.TemplateResponse(
        request=request,
        name="top5/detail.html",
        context={
            **bundle,
            "info": INFO_MESSAGES.get(info, info),
            "error": error,
        },
    )
