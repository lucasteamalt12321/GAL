from functools import lru_cache

from supabase import Client, ClientOptions, create_client

from app.config import get_settings
from app.services.auth import SupabaseNotConfiguredError

__all__ = ["SupabaseNotConfiguredError", "get_public_client", "get_service_client"]


def _client_options() -> ClientOptions:
    return ClientOptions(
        postgrest_client_timeout=get_settings().supabase_timeout_seconds,
        auto_refresh_token=False,
    )


@lru_cache
def get_public_client() -> Client | None:
    settings = get_settings()
    if not settings.supabase_configured:
        return None
    return create_client(
        settings.supabase_url,
        settings.supabase_anon_key,
        options=_client_options(),
    )


@lru_cache
def get_service_client() -> Client | None:
    settings = get_settings()
    if not settings.service_role_configured:
        return None
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
        options=_client_options(),
    )
