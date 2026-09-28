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
    weather_search_api_url: str = "https://api.weatherapi.com/v1/search.json"
    places_provider: Literal["google"] | None = None
    google_places_api_key: SecretStr | None = None
    google_places_text_search_url: str = (
        "https://places.googleapis.com/v1/places:searchText"
    )
    currency_provider: Literal["frankfurter"] | None = None
    frankfurter_base_url: str = "https://api.frankfurter.dev/v2"
    flight_provider: Literal["travelport"] | None = None
    flight_metadata_path: str | None = Field(
        default=None,
        min_length=1,
    )
    airport_directory_path: str | None = Field(
        default=None,
        min_length=1,
    )

    # Travelport
    travelport_environment: Literal["preproduction", "production"] = "preproduction"
    travelport_username: SecretStr | None = None
    travelport_password: SecretStr | None = None
    travelport_client_id: SecretStr | None = None
    travelport_client_secret: SecretStr | None = None
    travelport_pcc_core: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    travelport_max_response_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1024,
        le=20 * 1024 * 1024,
    )

    # LLM models
    groq_model: str = "openai/gpt-oss-20b"
    google_model: str = "gemini-2.5-flash"
    openai_model: str = "gpt-4.1-mini"

    # LLM providers
    groq_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

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
    max_search_results: int = Field(default=10, ge=1, le=100)
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
        """Require configuration for the enabled flight provider."""
        if self.flight_provider != "travelport":
            return self

        required_settings = {
            "TRAVELPORT_USERNAME": self.travelport_username,
            "TRAVELPORT_PASSWORD": self.travelport_password,
            "TRAVELPORT_CLIENT_ID": self.travelport_client_id,
            "TRAVELPORT_CLIENT_SECRET": self.travelport_client_secret,
            "TRAVELPORT_PCC_CORE": self.travelport_pcc_core,
            "FLIGHT_METADATA_PATH": self.flight_metadata_path,
            "AIRPORT_DIRECTORY_PATH": self.airport_directory_path,
        }

        missing_settings: list[str] = []

        for name, value in required_settings.items():
            raw_value = (
                value.get_secret_value() if isinstance(value, SecretStr) else value
            )

            if raw_value is None or not raw_value.strip():
                missing_settings.append(name)

        if missing_settings:
            raise ValueError(
                "Travelport flight provider requires: " + ", ".join(missing_settings)
            )

        return self

    @property
    def travelport_auth_url(self) -> str:
        if self.travelport_environment == "production":
            return "https://auth.travelport.net/oauth/token"
        return "https://auth.pp.travelport.net/oauth/token"

    @property
    def travelport_air_base_url(self) -> str:
        if self.travelport_environment == "production":
            return "https://api.travelport.net/11/air"

        return "https://api.pp.travelport.net/11/air"


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the application."""
    return Settings()
