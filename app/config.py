"""Typed application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    app_name: str = "Travel Assistant"
    app_env: Literal["local", "development", "staging", "production"] = "local"
    debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # FastApi
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    api_v1_prefix: str = "/api/v1"
    internal_mcp_path: str = "/internal/mcp"

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    # PostgreSQL
    database_url: SecretStr
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)

    # Authentication
    jwt_signing_key: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = Field(default=15, ge=1)
    refresh_token_ttl_days: int = Field(default=30, ge=1)

    # External APIs
    weather_api_key: SecretStr | None = None
    weather_api_url: str = "https://api.weatherapi.com/v1/current.json"
    tavily_api_key: SecretStr | None = None

    # LLM providers
    groq_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # LangSmith
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "travel-assistant-local"
    langsmith_tracing: bool = False

    # Request limits
    provider_timeout_seconds: float = Field(default=15.0, gt=0)
    model_timeout_seconds: float = Field(default=30.0, gt=0)
    max_search_results: int = Field(default=10, ge=1, le=100)
    max_model_attempts: int = Field(default=3, ge=1, le=5)


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the application."""
    return Settings()
