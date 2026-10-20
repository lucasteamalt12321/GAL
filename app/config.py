from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "GAL"
    app_version: str = "0.1.0"
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
