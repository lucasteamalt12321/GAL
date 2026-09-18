from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


class SupabaseNotConfiguredError(RuntimeError):
    pass


@lru_cache
def get_public_client() -> Client | None:
    settings = get_settings()
    if not settings.supabase_configured:
        return None
    return create_client(settings.supabase_url, settings.supabase_anon_key)


@lru_cache
def get_service_client() -> Client | None:
    settings = get_settings()
    if not settings.service_role_configured:
        return None
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
