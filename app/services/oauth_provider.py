"""OAuth 2.0 provider for first-party clients (LTHub).

Authorization-code flow with PKCE (S256) for a public client:

    GET  /oauth/authorize   consent page (uses the GAL web session)
    POST /oauth/authorize   issue a short-lived one-time code
    POST /oauth/token       exchange code + code_verifier for a token
    GET  /oauth/userinfo    resolve a bearer token to a GAL profile

Codes and tokens are stored as SHA-256 hashes in tables reachable only through
the service role (``public.oauth_codes`` / ``public.oauth_tokens``).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import time

from app.config import get_settings
from app.database.connection import SupabaseNotConfiguredError, get_service_client

logger = logging.getLogger(__name__)

OAUTH_SCOPE = "profile"
CODE_TTL_SECONDS = 300
TOKEN_TTL_SECONDS = 30 * 86400

DEFAULT_LTHUB_REDIRECT_URIS = {
    "https://lthub.vercel.app/api/auth/gal/callback",
    "http://127.0.0.1:5000/api/auth/gal/callback",
    "http://localhost:5000/api/auth/gal/callback",
    "http://127.0.0.1:8000/api/auth/gal/callback",
    "http://localhost:8000/api/auth/gal/callback",
}

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class OAuthError(Exception):
    """RFC 6749 error code that the token endpoint returns to the client."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> int:
    return int(time.time())


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def enabled() -> bool:
    return get_settings().oauth_provider_enabled


def _clients() -> dict[str, dict]:
    settings = get_settings()
    redirect_uris = set(DEFAULT_LTHUB_REDIRECT_URIS)
    for uri in (settings.oauth_lthub_redirect_uris or "").split(","):
        uri = uri.strip()
        if uri:
            redirect_uris.add(uri)
    client_id = (settings.oauth_lthub_client_id or "lthub").strip()
    return {
        client_id: {
            "name": "LTHub",
            "redirect_uris": redirect_uris,
            "public": True,
        }
    }


def client_name(client_id: str) -> str | None:
    client = _clients().get(client_id)
    return client["name"] if client else None


def validate_authorize(
    client_id: str,
    redirect_uri: str,
    response_type: str,
    code_challenge: str,
    code_challenge_method: str,
) -> str | None:
    """Return an error code or ``None`` when the request is acceptable."""
    if response_type != "code":
        return "unsupported_response_type"
    if not CLIENT_ID_RE.fullmatch(client_id or ""):
        return "invalid_client"
    client = _clients().get(client_id)
    if client is None:
        return "invalid_client"
    if redirect_uri not in client["redirect_uris"]:
        return "invalid_redirect_uri"
    if not CHALLENGE_RE.fullmatch(code_challenge or ""):
        return "invalid_code_challenge"
    if code_challenge_method != "S256":
        return "invalid_code_challenge_method"
    return None


def _client():
    client = get_service_client()
    if client is None:
        raise SupabaseNotConfiguredError("Supabase service role is not configured")
    return client


def _rows(response) -> list[dict]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else []


def _one(response) -> dict | None:
    rows = _rows(response)
    return rows[0] if rows else None


def _cleanup(client) -> None:
    cutoff = _now() - 86400
    try:
        client.table("oauth_codes").delete().lt("expires_at", cutoff).execute()
        client.table("oauth_tokens").delete().lt("expires_at", cutoff).execute()
    except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
        logger.debug("OAuth cleanup failed: %s", exc)


def _profile(client, profile_id: str) -> dict | None:
    response = (
        client.table("profiles")
        .select("id,username,display_name,avatar_url")
        .eq("id", profile_id)
        .limit(1)
        .execute()
    )
    return _one(response)


def public_profile(profile: dict) -> dict:
    username = str(profile.get("username") or "")
    display_name = str(profile.get("display_name") or username)
    return {
        "sub": str(profile["id"]),
        "preferred_username": username,
        "login": username,
        "name": display_name,
        "display_name": display_name,
        "avatar_url": profile.get("avatar_url"),
    }


def issue_code(
    *,
    profile_id: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
) -> str:
    client = _client()
    code = secrets.token_urlsafe(32)
    client.table("oauth_codes").insert(
        {
            "code_hash": hash_value(code),
            "client_id": client_id,
            "profile_id": profile_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "expires_at": _now() + CODE_TTL_SECONDS,
        }
    ).execute()
    _cleanup(client)
    return code


def exchange_code(
    *,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    client = _client()
    code_hash = hash_value(code)
    row = _one(
        client.table("oauth_codes")
        .select("*")
        .eq("code_hash", code_hash)
        .limit(1)
        .execute()
    )
    if not row or row.get("used_at") is not None or int(row["expires_at"]) < _now():
        raise OAuthError("invalid_grant")
    if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
        raise OAuthError("invalid_grant")
    if not VERIFIER_RE.fullmatch(code_verifier or ""):
        raise OAuthError("invalid_code_verifier")
    if pkce_challenge(code_verifier) != row["code_challenge"]:
        raise OAuthError("invalid_code_verifier")

    consumed = (
        client.table("oauth_codes")
        .update({"used_at": _now()})
        .eq("code_hash", code_hash)
        .is_("used_at", "null")
        .execute()
    )
    if len(_rows(consumed)) != 1:
        raise OAuthError("invalid_grant")

    profile = _profile(client, row["profile_id"])
    if profile is None:
        raise OAuthError("invalid_grant")

    access_token = secrets.token_urlsafe(32)
    now = _now()
    client.table("oauth_tokens").insert(
        {
            "token_hash": hash_value(access_token),
            "client_id": client_id,
            "profile_id": profile["id"],
            "created_at": now,
            "expires_at": now + TOKEN_TTL_SECONDS,
        }
    ).execute()
    _cleanup(client)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "scope": OAUTH_SCOPE,
        "user": public_profile(profile),
    }


def resolve_token(access_token: str) -> dict | None:
    client = _client()
    row = _one(
        client.table("oauth_tokens")
        .select("*")
        .eq("token_hash", hash_value(access_token))
        .limit(1)
        .execute()
    )
    if not row or row.get("revoked_at") is not None or int(row["expires_at"]) < _now():
        return None
    profile = _profile(client, row["profile_id"])
    if profile is None:
        return None
    return public_profile(profile)
