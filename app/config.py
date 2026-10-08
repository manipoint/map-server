"""Typed application configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
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
    database_connection_mode: Literal["url", "cloud_sql"] = "url"

    # PostgreSQL URL (local or hosted, including Neon)
    database_url: SecretStr | None = None

    # Google Cloud SQL
    cloud_sql_instance_connection_name: str | None = None
    cloud_sql_ip_type: Literal["public", "private"] = "public"
    database_user: str | None = None
    database_name: str | None = None
    database_password: SecretStr | None = None

    # SQLAlchemy connection pool
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)
    database_connect_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    database_command_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    database_readiness_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30,
    )

    # Authentication
    jwt_signing_key: SecretStr = Field(
        min_length=32,
    )
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "travel-assistant-api"
    jwt_audience: str = "travel-assistant-flutter"
    refresh_token_hash_key: SecretStr = Field(min_length=32)
    access_token_ttl_minutes: int = Field(default=15, ge=1)
    refresh_token_ttl_days: int = Field(default=30, ge=1)

    # External APIs
    weather_api_key: SecretStr | None = None
    weather_api_url: str = "https://api.weatherapi.com/v1/current.json"
    weather_forecast_api_url: str = "https://api.weatherapi.com/v1/forecast.json"
    weather_search_api_url: str = "https://api.weatherapi.com/v1/search.json"
    places_provider: Literal["google"] | None = None
    google_places_api_key: SecretStr | None = None
    google_places_text_search_url: str = (
        "https://places.googleapis.com/v1/places:searchText"
    )
    currency_provider: Literal["frankfurter"] | None = None
    frankfurter_base_url: str = "https://api.frankfurter.dev/v2"
    flight_provider: Literal["serpapi"] | None = None
    hotel_provider: Literal["serpapi"] | None = None
    serpapi_api_key: SecretStr | None = None
    serpapi_search_url: str = "https://serpapi.com/search.json"
    serpapi_max_response_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1024,
        le=20 * 1024 * 1024,
    )
    flight_metadata_path: str | None = Field(
        default=None,
        min_length=1,
    )
    airport_directory_path: str | None = Field(
        default=None,
        min_length=1,
    )

    # LLM models
    google_model: str = "gemini-3.8-flash"

    # LLM providers
    google_api_key: SecretStr | None = None

    # LangSmith
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "travel-assistant-local"
    langsmith_tracing: bool = False
    langsmith_endpoint: Literal[
        "https://api.smith.langchain.com", "https://eu.api.smith.langchain.com"
    ] = "https://api.smith.langchain.com"
    langsmith_tracing_sampling_rate: float = Field(default=1.0, ge=0, le=1)

    # Request limits
    provider_timeout_seconds: float = Field(default=15.0, gt=0)
    model_timeout_seconds: float = Field(default=30.0, gt=0)
    model_max_output_tokens: int = Field(default=8192, ge=512, le=32768)
    model_max_input_chars: int = Field(default=120000, ge=4000, le=500000)
    generation_global_concurrency: int = Field(default=16, ge=1, le=1000)
    generation_user_concurrency: int = Field(default=2, ge=1, le=20)
    generation_daily_request_limit: int = Field(default=100, ge=1, le=10000)
    websocket_max_pending_requests: int = Field(default=4, ge=1, le=20)
    websocket_auth_check_seconds: float = Field(default=15.0, gt=0, le=60)
    max_search_results: int = Field(default=100, ge=1, le=100)
    max_model_attempts: int = Field(default=3, ge=1, le=5)
    websocket_max_message_bytes: int = Field(
        default=64 * 1024,
        ge=1024,
        le=1024 * 1024,
    )
    websocket_heartbeat_interval_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
    )
    websocket_idle_timeout_seconds: float = Field(
        default=90.0,
        ge=10.0,
        le=600.0,
    )
    conversation_history_message_limit: int = Field(default=20, ge=1, le=100)
    assistant_run_lease_seconds: int = Field(default=120, ge=30, le=900)
    travel_response_timeout_seconds: float = Field(default=75.0, gt=0, le=600)
    assistant_run_completion_margin_seconds: float = Field(
        default=15.0,
        ge=5.0,
        le=120.0,
    )
    max_tool_rounds: int = Field(default=2, ge=1, le=5)

    @model_validator(mode="after")
    def validate_database_configuration(self) -> Self:
        """Ensure the selected database mode has all required settings."""
        if self.database_connection_mode == "url":
            if self.database_url is None:
                raise ValueError("DATABASE_URL is required in url mode")
            return self

        required_cloud_sql_settings = {
            "CLOUD_SQL_INSTANCE_CONNECTION_NAME": (
                self.cloud_sql_instance_connection_name
            ),
            "DATABASE_USER": self.database_user,
            "DATABASE_NAME": self.database_name,
            "DATABASE_PASSWORD": self.database_password,
        }
        missing_settings = [
            name for name, value in required_cloud_sql_settings.items() if value is None
        ]
        if missing_settings:
            missing_names = ", ".join(missing_settings)
            raise ValueError(
                f"Cloud SQL mode requires the following settings: {missing_names}"
            )
        return self

    @model_validator(mode="after")
    def validate_websocket_configuration(self) -> Self:
        """Ensure heartbeat timing leaves enough room before idle timeout."""
        if (
            self.websocket_heartbeat_interval_seconds
            >= self.websocket_idle_timeout_seconds
        ):
            raise ValueError(
                "WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS must be less than "
                "WEBSOCKET_IDLE_TIMEOUT_SECONDS"
            )
        return self

    @model_validator(mode="after")
    def validate_assistant_run_lease(self) -> Self:
        """Ensure the lease outlives the complete graph and persistence margin."""

        minimum_lease_seconds = (
            self.travel_response_timeout_seconds
            + self.assistant_run_completion_margin_seconds
        )
        if self.assistant_run_lease_seconds <= minimum_lease_seconds:
            raise ValueError(
                "ASSISTANT_RUN_LEASE_SECONDS must be greater than "
                "TRAVEL_RESPONSE_TIMEOUT_SECONDS plus "
                "ASSISTANT_RUN_COMPLETION_MARGIN_SECONDS"
            )
        return self

    @model_validator(mode="after")
    def validate_places_provider_configuration(self) -> Self:
        """Require credentials for the enabled places provider."""

        if self.places_provider == "google" and self.google_places_api_key is None:
            raise ValueError(
                "GOOGLE_PLACES_API_KEY is required "
                "when Google Places provider is enabled"
            )

        return self

    @model_validator(mode="after")
    def validate_flight_provider_configuration(self) -> Self:
        """Require shared credentials and timezone data for enabled searches."""
        if self.flight_provider is None and self.hotel_provider is None:
            return self

        if self.serpapi_api_key is None:
            raise ValueError("SERPAPI_API_KEY is required for SerpApi searches")

        if self.flight_provider == "serpapi":
            missing_data = [
                name
                for name, value in (
                    ("FLIGHT_METADATA_PATH", self.flight_metadata_path),
                    ("AIRPORT_DIRECTORY_PATH", self.airport_directory_path),
                )
                if value is None or not value.strip()
            ]
            if missing_data:
                raise ValueError(
                    "SerpApi flight search requires: " + ", ".join(missing_data)
                )
        if self.hotel_provider == "serpapi" and self.weather_api_key is None:
            raise ValueError(
                "SerpApi hotel search requires WEATHER_API_KEY "
                "for destination resolution"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the application."""
    return Settings()
