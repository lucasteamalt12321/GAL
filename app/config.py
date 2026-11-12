from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "GAL"
    app_version: str = "1.8.0"
    app_env: str = "development"
    app_url: str = ""

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    # Таймаут одного PostgREST-запроса (сек). Библиотека по умолчанию ждёт
    # 120 с, поэтому при сбое Supabase страница висела минуты и падала с
    # httpx.ReadTimeout → 500. Держим значение с запасом ниже лимита функции.
    supabase_timeout_seconds: float = 6.0

    # Часовой пояс отображения дат в UI (смещение от UTC, часы).
    display_utc_offset_hours: int = 3

    # Кластерный бутстрэп для погрешности Bradley–Terry. 0 отключает расчёт;
    # фактическое число реплик дополнительно ограничивается объёмом данных.
    bt_bootstrap_replicates: int = 200
    bt_bootstrap_seed: int = 20260101

    # LTHub OAuth (публичный PKCE-клиент, без client_secret).
    lthub_oauth_enabled: bool = True
    lthub_client_id: str = "gal"
    lthub_authorize_url: str = "https://lthub.vercel.app/oauth/authorize"
    lthub_token_url: str = "https://lthub.vercel.app/oauth/token"
    lthub_userinfo_url: str = "https://lthub.vercel.app/oauth/userinfo"
    # Пусто — берётся APP_URL (или origin запроса) + /auth/lthub/callback.
    lthub_redirect_uri: str = ""

    # OAuth-провайдер GAL для LTHub (authorization code + PKCE, public client).
    oauth_provider_enabled: bool = True
    oauth_lthub_client_id: str = "lthub"
    # Дополнительные redirect_uri через запятую (базовые заданы в сервисе).
    oauth_lthub_redirect_uris: str = ""

    @property
    def is_production(self) -> bool:
        """Единая точка истины для production-поведения.

        Раньше сравнение было строгим (`app_env == "production"`), поэтому
        опечатка (`prod`, `Production`, значение с пробелом) молча выключала
        CSRF-защиту и флаг Secure у cookies — fail-open.
        """
        return self.app_env.strip().lower() == "production"

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)

    @property
    def service_role_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
