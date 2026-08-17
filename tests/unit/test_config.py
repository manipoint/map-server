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


@pytest.mark.parametrize("history_limit", [0, 101])
def test_conversation_history_limit_rejects_values_outside_bounds(
    history_limit: int,
) -> None:
    """Configuration should prevent empty or excessively large model history."""

    with pytest.raises(ValidationError):
        create_settings(conversation_history_message_limit=history_limit)
