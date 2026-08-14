"""Tests for application configuration."""

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings


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
