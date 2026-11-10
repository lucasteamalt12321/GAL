"""OAuth 2.0 provider endpoints consumed by LTHub."""

from __future__ import annotations

import logging
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.forms import body_data
from app.services import oauth_provider as provider
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

_PARAM_NAMES = (
    "client_id",
    "redirect_uri",
    "response_type",
    "code_challenge",
    "code_challenge_method",
    "state",
    "scope",
)


def _authorize_params(source) -> dict[str, str]:
    return {name: str(source.get(name) or "") for name in _PARAM_NAMES}


def _login_redirect(params: dict[str, str]) -> RedirectResponse:
    query = urlencode({name: value for name, value in params.items() if value})
    target = "/oauth/authorize" + (f"?{query}" if query else "")
    return RedirectResponse(
        "/auth/login?next=" + quote(target, safe=""), status_code=303
    )


def _redirect_with(redirect_uri: str, state: str, **params) -> RedirectResponse:
    query = {key: value for key, value in params.items() if value}
    if state:
        query["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        redirect_uri + separator + urlencode(query), status_code=303
    )


def _error_page(request: Request, detail: str, status_code: int = 400) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="errors/error.html",
        context={"status_code": status_code, "detail": detail},
        status_code=status_code,
    )


@router.get("/oauth/authorize", response_class=HTMLResponse, include_in_schema=False)
def authorize_page(request: Request) -> Response:
    if not provider.enabled():
        return _error_page(request, "OAuth-провайдер отключён.", 404)
    params = _authorize_params(request.query_params)
    error = provider.validate_authorize(
        params["client_id"],
        params["redirect_uri"],
        params["response_type"],
        params["code_challenge"],
        params["code_challenge_method"],
    )
    if error:
        return _error_page(request, f"Некорректный запрос авторизации: {error}.")
    user = request.state.user
    if user is None:
        return _login_redirect(params)
    return templates.TemplateResponse(
        request=request,
        name="oauth/authorize.html",
        context={
            "client_name": provider.client_name(params["client_id"]),
            "params": params,
            "profile": user,
        },
    )


@router.post("/oauth/authorize", response_class=HTMLResponse, include_in_schema=False)
async def authorize_submit(request: Request) -> Response:
    if not provider.enabled():
        return _error_page(request, "OAuth-провайдер отключён.", 404)
    data = await body_data(request)
    params = _authorize_params(data)
    error = provider.validate_authorize(
        params["client_id"],
        params["redirect_uri"],
        params["response_type"],
        params["code_challenge"],
        params["code_challenge_method"],
    )
    if error:
        return _error_page(request, f"Некорректный запрос авторизации: {error}.")
    user = request.state.user
    if user is None:
        return _login_redirect(params)
    if str(data.get("decision") or "") != "allow":
        return _redirect_with(
            params["redirect_uri"], params["state"], error="access_denied"
        )
    code = provider.issue_code(
        profile_id=str(user.id),
        client_id=params["client_id"],
        redirect_uri=params["redirect_uri"],
        code_challenge=params["code_challenge"],
    )
    return _redirect_with(params["redirect_uri"], params["state"], code=code)


@router.post("/oauth/token", response_class=JSONResponse)
async def token(request: Request) -> JSONResponse:
    data = await body_data(request)
    if str(data.get("grant_type") or "authorization_code") != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    client_id = str(data.get("client_id") or "").strip()
    redirect_uri = str(data.get("redirect_uri") or "").strip()
    code = str(data.get("code") or "").strip()
    verifier = str(data.get("code_verifier") or "").strip()
    if not provider.client_name(client_id):
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    try:
        payload = provider.exchange_code(
            client_id=client_id,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
        )
    except provider.OAuthError as exc:
        return JSONResponse({"error": exc.code}, status_code=400)
    except Exception as exc:  # noqa: BLE001 - JSON API must not leak an HTML 500
        logger.warning("OAuth token exchange failed: %s", exc)
        return JSONResponse({"error": "server_error"}, status_code=503)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.get("/oauth/userinfo", response_class=JSONResponse)
def userinfo(request: Request) -> JSONResponse:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return JSONResponse(
            {"error": "invalid_token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = header[7:].strip()
    try:
        profile = provider.resolve_token(token) if token else None
    except Exception as exc:  # noqa: BLE001 - JSON API must not leak an HTML 500
        logger.warning("OAuth userinfo failed: %s", exc)
        return JSONResponse({"error": "server_error"}, status_code=503)
    if profile is None:
        return JSONResponse(
            {"error": "invalid_token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return JSONResponse(profile, headers={"Cache-Control": "no-store"})


@router.get("/.well-known/oauth-authorization-server", response_class=JSONResponse)
def metadata(request: Request) -> JSONResponse:
    base = str(request.base_url).rstrip("/")
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": base + "/oauth/authorize",
            "token_endpoint": base + "/oauth/token",
            "userinfo_endpoint": base + "/oauth/userinfo",
            "scopes_supported": [provider.OAUTH_SCOPE],
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )
