"""Tests for application configuration."""

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings


def create_settings(**overrides) -> Settings:
    """Create deterministic URL-mode settings with optional overrides."""

    values = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
    }
    values.update(overrides)
    return Settings(**values)


def test_url_database_mode_requires_database_url() -> None:
    """URL mode should reject a missing database URL."""

    with pytest.raises(ValidationError, match="DATABASE_URL is required"):
        Settings(
            _env_file=None,
            database_connection_mode="url",
            database_url=None,
            jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
            refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
        )


def test_cloud_sql_mode_accepts_complete_configuration() -> None:
    """Cloud SQL mode should accept all required connection settings."""

    settings = Settings(
        _env_file=None,
        database_connection_mode="cloud_sql",
        database_url=None,
        cloud_sql_instance_connection_name=(
            "travel-assistant-505317:asia-south1:free-trial-first-project"
        ),
        database_user="travel_app",
        database_name="travel_assistant",
        database_password=SecretStr("test-database-password"),
        jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
        refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
    )

    assert settings.database_connection_mode == "cloud_sql"
    assert settings.database_user == "travel_app"
    assert settings.database_name == "travel_assistant"


def test_cloud_sql_mode_rejects_missing_settings() -> None:
    """Cloud SQL mode should report incomplete configuration."""

    with pytest.raises(
        ValidationError,
        match="CLOUD_SQL_INSTANCE_CONNECTION_NAME",
    ):
        Settings(
            _env_file=None,
            database_connection_mode="cloud_sql",
            database_url=None,
            cloud_sql_instance_connection_name=None,
            database_user=None,
            database_name=None,
            database_password=None,
            jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
            refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
        )


def test_database_readiness_timeout_uses_a_safe_default() -> None:
    """Readiness should fail quickly instead of waiting on the normal pool timeout."""

    settings = create_settings()

    assert settings.database_readiness_timeout_seconds == 2.0


@pytest.mark.parametrize("timeout_seconds", [0, -1, 10.1])
def test_database_readiness_timeout_rejects_unsafe_values(
    timeout_seconds: float,
) -> None:
    """Readiness timeout must stay positive and operationally bounded."""

    with pytest.raises(ValidationError):
        create_settings(database_readiness_timeout_seconds=timeout_seconds)


def test_websocket_limits_have_safe_defaults() -> None:
    """Local settings should use the documented message and heartbeat limits."""

    settings = create_settings()

    assert settings.websocket_max_message_bytes == 64 * 1024
    assert settings.websocket_heartbeat_interval_seconds == 30.0
    assert settings.websocket_idle_timeout_seconds == 90.0


def test_websocket_limits_load_from_environment(monkeypatch) -> None:
    """Deployment environment variables should override WebSocket defaults."""

    monkeypatch.setenv("WEBSOCKET_MAX_MESSAGE_BYTES", "32768")
    monkeypatch.setenv("WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", "20")
    monkeypatch.setenv("WEBSOCKET_IDLE_TIMEOUT_SECONDS", "75")

    settings = create_settings()

    assert settings.websocket_max_message_bytes == 32768
    assert settings.websocket_heartbeat_interval_seconds == 20.0
    assert settings.websocket_idle_timeout_seconds == 75.0


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("websocket_max_message_bytes", 1023),
        ("websocket_max_message_bytes", 1024 * 1024 + 1),
        ("websocket_heartbeat_interval_seconds", 4.9),
        ("websocket_heartbeat_interval_seconds", 300.1),
        ("websocket_idle_timeout_seconds", 9.9),
        ("websocket_idle_timeout_seconds", 600.1),
    ],
)
def test_websocket_limits_reject_values_outside_bounds(
    field_name: str,
    invalid_value: int | float,
) -> None:
    """Unsafe message sizes and heartbeat timings should fail configuration."""

    with pytest.raises(ValidationError):
        create_settings(**{field_name: invalid_value})


@pytest.mark.parametrize(
    ("heartbeat_seconds", "idle_seconds"),
    [(90.0, 90.0), (91.0, 90.0)],
)
def test_websocket_heartbeat_must_be_shorter_than_idle_timeout(
    heartbeat_seconds: float,
    idle_seconds: float,
) -> None:
    """The server must allow at least some time after each expected heartbeat."""

    with pytest.raises(
        ValidationError,
        match="WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS",
    ):
        create_settings(
            websocket_heartbeat_interval_seconds=heartbeat_seconds,
            websocket_idle_timeout_seconds=idle_seconds,
        )


def test_websocket_boundary_timings_are_accepted() -> None:
    """Valid minimum heartbeat and idle-timeout values should be accepted."""

    settings = create_settings(
        websocket_heartbeat_interval_seconds=5.0,
        websocket_idle_timeout_seconds=10.0,
    )

    assert settings.websocket_heartbeat_interval_seconds == 5.0
    assert settings.websocket_idle_timeout_seconds == 10.0


def test_conversation_history_uses_a_bounded_default() -> None:
    """Default model context should include only recent conversation messages."""

    settings = create_settings()

    assert settings.conversation_history_message_limit == 20


def test_tool_rounds_use_a_cost_bounded_default() -> None:
    """A request should allow limited tool use without an unbounded model loop."""

    settings = create_settings()

    assert settings.max_tool_rounds == 2


@pytest.mark.parametrize("tool_rounds", [0, 6])
def test_tool_round_limit_rejects_values_outside_bounds(tool_rounds: int) -> None:
    """Configuration should reject disabled or excessively costly tool loops."""

    with pytest.raises(ValidationError):
        create_settings(max_tool_rounds=tool_rounds)


@pytest.mark.parametrize("history_limit", [0, 101])
def test_conversation_history_limit_rejects_values_outside_bounds(
    history_limit: int,
) -> None:
    """Configuration should prevent empty or excessively large model history."""

    with pytest.raises(ValidationError):
        create_settings(conversation_history_message_limit=history_limit)


def test_assistant_run_lease_uses_a_safe_default() -> None:
    """The lease should outlive the graph deadline and completion margin."""

    settings = create_settings()

    assert settings.assistant_run_lease_seconds == 120
    assert settings.travel_response_timeout_seconds == 75.0
    assert settings.assistant_run_completion_margin_seconds == 15.0
    assert settings.assistant_run_lease_seconds > (
        settings.travel_response_timeout_seconds
        + settings.assistant_run_completion_margin_seconds
    )


@pytest.mark.parametrize(
    ("lease_seconds", "graph_timeout_seconds", "completion_margin_seconds"),
    [(30, 20.0, 5.0), (900, 600.0, 120.0)],
)
def test_assistant_run_lease_accepts_documented_boundaries(
    lease_seconds: int,
    graph_timeout_seconds: float,
    completion_margin_seconds: float,
) -> None:
    """The configured lease range should include both documented endpoints."""

    settings = create_settings(
        assistant_run_lease_seconds=lease_seconds,
        travel_response_timeout_seconds=graph_timeout_seconds,
        assistant_run_completion_margin_seconds=completion_margin_seconds,
    )

    assert settings.assistant_run_lease_seconds == lease_seconds


@pytest.mark.parametrize("lease_seconds", [29, 901])
def test_assistant_run_lease_rejects_values_outside_its_bounds(
    lease_seconds: int,
) -> None:
    """Leases that are too short or too long should fail configuration."""

    with pytest.raises(ValidationError):
        create_settings(assistant_run_lease_seconds=lease_seconds)


@pytest.mark.parametrize(
    ("lease_seconds", "graph_timeout_seconds", "completion_margin_seconds"),
    [(30, 25.0, 5.0), (30, 26.0, 5.0)],
)
def test_assistant_run_lease_must_outlast_graph_and_completion_margin(
    lease_seconds: int,
    graph_timeout_seconds: float,
    completion_margin_seconds: float,
) -> None:
    """A worker must own its claim through graph execution and persistence."""

    with pytest.raises(
        ValidationError,
        match=(
            "ASSISTANT_RUN_LEASE_SECONDS must be greater than "
            "TRAVEL_RESPONSE_TIMEOUT_SECONDS"
        ),
    ):
        create_settings(
            assistant_run_lease_seconds=lease_seconds,
            travel_response_timeout_seconds=graph_timeout_seconds,
            assistant_run_completion_margin_seconds=completion_margin_seconds,
        )


@pytest.mark.parametrize("graph_timeout_seconds", [0, 601])
def test_travel_response_timeout_rejects_values_outside_bounds(
    graph_timeout_seconds: float,
) -> None:
    """Graph execution must have a positive bounded end-to-end deadline."""

    with pytest.raises(ValidationError):
        create_settings(travel_response_timeout_seconds=graph_timeout_seconds)


@pytest.mark.parametrize("completion_margin_seconds", [4.9, 120.1])
def test_assistant_completion_margin_rejects_values_outside_bounds(
    completion_margin_seconds: float,
) -> None:
    """Persistence must retain a small bounded lease margin after the graph."""

    with pytest.raises(ValidationError):
        create_settings(
            assistant_run_completion_margin_seconds=completion_margin_seconds
        )


def test_flight_provider_is_disabled_without_credentials_by_default() -> None:
    """Non-flight deployments should start without requiring a Duffel token."""

    settings = create_settings()

    assert settings.flight_provider is None
    assert settings.hotel_provider is None
    assert settings.duffel_api_key is None
    assert settings.duffel_base_url == "https://api.duffel.com"
    assert settings.duffel_api_version == "v2"
    assert settings.duffel_supplier_timeout_ms == 10_000
    assert settings.duffel_stays_radius_km == 5


def test_duffel_provider_requires_an_api_key() -> None:
    """Enabling Duffel without credentials should fail during startup."""

    with pytest.raises(
        ValidationError,
        match="DUFFEL_API_KEY is required when a Duffel provider is enabled",
    ):
        create_settings(
            flight_provider="duffel",
            duffel_api_key=None,
        )


def test_duffel_hotel_provider_requires_an_api_key() -> None:
    """Stays cannot be enabled without shared Duffel credentials."""

    with pytest.raises(
        ValidationError,
        match="DUFFEL_API_KEY is required when a Duffel provider is enabled",
    ):
        create_settings(
            hotel_provider="duffel",
            duffel_api_key=None,
        )


def test_duffel_hotel_provider_accepts_shared_credentials() -> None:
    """Hotel-only deployments should reuse one Duffel token."""

    settings = create_settings(
        hotel_provider="duffel",
        duffel_api_key=SecretStr("test-duffel-token"),
        duffel_stays_radius_km=10,
        provider_timeout_seconds=5.0,
    )

    assert settings.flight_provider is None
    assert settings.hotel_provider == "duffel"
    assert settings.duffel_stays_radius_km == 10


def test_duffel_flights_and_hotels_can_be_enabled_together() -> None:
    """One shared token should support both configured Duffel products."""

    settings = create_settings(
        flight_provider="duffel",
        hotel_provider="duffel",
        duffel_api_key=SecretStr("test-duffel-token"),
    )

    assert settings.flight_provider == "duffel"
    assert settings.hotel_provider == "duffel"


@pytest.mark.parametrize("radius_km", [0, 101])
def test_duffel_stays_radius_rejects_unsupported_values(radius_km: int) -> None:
    """Hotel radius should remain inside Duffel's documented range."""

    with pytest.raises(ValidationError):
        create_settings(duffel_stays_radius_km=radius_km)


def test_duffel_provider_accepts_safe_complete_configuration() -> None:
    """A token and shorter supplier timeout should enable flight search."""

    settings = create_settings(
        flight_provider="duffel",
        duffel_api_key=SecretStr("test-duffel-token"),
        duffel_supplier_timeout_ms=10_000,
        provider_timeout_seconds=15.0,
    )

    assert settings.flight_provider == "duffel"
    assert settings.duffel_api_key is not None
    assert settings.duffel_api_key.get_secret_value() == "test-duffel-token"


@pytest.mark.parametrize(
    ("supplier_timeout_ms", "http_timeout_seconds"),
    [(15_000, 15.0), (16_000, 15.0)],
)
def test_duffel_supplier_timeout_must_be_shorter_than_http_timeout(
    supplier_timeout_ms: int,
    http_timeout_seconds: float,
) -> None:
    """HTTP should retain enough time to receive and parse Duffel's response."""

    with pytest.raises(
        ValidationError,
        match="DUFFEL_SUPPLIER_TIMEOUT_MS must be less than",
    ):
        create_settings(
            flight_provider="duffel",
            duffel_api_key=SecretStr("test-duffel-token"),
            duffel_supplier_timeout_ms=supplier_timeout_ms,
            provider_timeout_seconds=http_timeout_seconds,
        )


@pytest.mark.parametrize("supplier_timeout_ms", [1_999, 60_001])
def test_duffel_supplier_timeout_rejects_provider_unsupported_values(
    supplier_timeout_ms: int,
) -> None:
    """Supplier timeout should stay inside Duffel's documented range."""

    with pytest.raises(ValidationError):
        create_settings(duffel_supplier_timeout_ms=supplier_timeout_ms)


def test_duffel_configuration_loads_from_environment(monkeypatch) -> None:
    """Deployment variables should activate Duffel without exposing its token."""

    monkeypatch.setenv("FLIGHT_PROVIDER", "duffel")
    monkeypatch.setenv("HOTEL_PROVIDER", "duffel")
    monkeypatch.setenv("DUFFEL_API_KEY", "test-duffel-token")
    monkeypatch.setenv("DUFFEL_STAYS_RADIUS_KM", "8")

    settings = create_settings()

    assert settings.flight_provider == "duffel"
    assert settings.hotel_provider == "duffel"
    assert settings.duffel_stays_radius_km == 8
    assert settings.duffel_api_key is not None


def test_places_provider_is_disabled_without_credentials_by_default() -> None:
    """Deployments without place discovery should not require a Tavily key."""

    settings = create_settings()

    assert settings.places_provider is None
    assert settings.tavily_api_key is None
    assert settings.tavily_search_api_url == "https://api.tavily.com/search"


def test_currency_provider_is_disabled_by_default() -> None:
    """Currency conversion should remain optional without requiring a key."""

    settings = create_settings()

    assert settings.currency_provider is None
    assert settings.frankfurter_base_url == "https://api.frankfurter.dev/v2"


def test_frankfurter_currency_provider_requires_no_api_key() -> None:
    """The public Frankfurter adapter should enable without credentials."""

    settings = create_settings(currency_provider="frankfurter")

    assert settings.currency_provider == "frankfurter"


def test_tavily_places_provider_requires_an_api_key() -> None:
    """Enabling Tavily place discovery should require credentials at startup."""

    with pytest.raises(
        ValidationError,
        match="TAVILY_API_KEY is required",
    ):
        create_settings(
            places_provider="tavily",
            tavily_api_key=None,
        )


def test_tavily_places_provider_accepts_complete_configuration() -> None:
    """A configured Tavily token and endpoint should enable place discovery."""

    settings = create_settings(
        places_provider="tavily",
        tavily_api_key=SecretStr("test-tavily-token"),
    )

    assert settings.places_provider == "tavily"
    assert settings.tavily_api_key is not None
    assert settings.tavily_api_key.get_secret_value() == "test-tavily-token"
    assert settings.tavily_search_api_url == "https://api.tavily.com/search"


def test_google_places_provider_requires_an_api_key() -> None:
    """Enabling Google place discovery should require credentials at startup."""

    with pytest.raises(
        ValidationError,
        match="GOOGLE_PLACES_API_KEY is required",
    ):
        create_settings(
            places_provider="google",
            google_places_api_key=None,
        )


def test_google_places_provider_accepts_complete_configuration() -> None:
    """A configured Google key and endpoint should enable place discovery."""

    settings = create_settings(
        places_provider="google",
        google_places_api_key=SecretStr("test-google-places-key"),
    )

    assert settings.places_provider == "google"
    assert settings.google_places_api_key is not None
    assert settings.google_places_api_key.get_secret_value() == (
        "test-google-places-key"
    )
    assert settings.google_places_text_search_url == (
        "https://places.googleapis.com/v1/places:searchText"
    )
