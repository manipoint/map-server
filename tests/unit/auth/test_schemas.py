"""Tests for authentication request and response schemas."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenPairResponse,
)


def test_register_normalizes_email_and_device_name() -> None:
    """Registration input should normalize human-entered values."""

    request = RegisterRequest(
        email="  User@Example.COM ",
        password="correct horse battery staple",
        device_id="550e8400-e29b-41d4-a716-446655440000",
        device_name="  Imran's iPhone  ",
    )

    assert request.email == "user@example.com"
    assert request.device_name == "Imran's iPhone"


def test_register_rejects_short_password() -> None:
    """New accounts should require a sufficiently long password."""

    with pytest.raises(ValidationError, match="at least 12"):
        RegisterRequest(
            email="user@example.com",
            password="short",
            device_id="550e8400-e29b-41d4-a716-446655440000",
        )


def test_login_accepts_existing_short_password() -> None:
    """Login should remain compatible with older password policies."""

    request = LoginRequest(
        email="user@example.com",
        password="old",
        device_id="550e8400-e29b-41d4-a716-446655440000",
    )

    assert request.password.get_secret_value() == "old"


def test_device_id_rejects_surrounding_whitespace() -> None:
    """Stable device identifiers should be stored exactly."""

    with pytest.raises(
        ValidationError,
        match="surrounding whitespace",
    ):
        LoginRequest(
            email="user@example.com",
            password="password",
            device_id=" 550e8400-e29b-41d4-a716-446655440000 ",
        )


def test_invalid_email_is_rejected() -> None:
    """Malformed email addresses should fail request validation."""

    with pytest.raises(ValidationError):
        RegisterRequest(
            email="not-an-email",
            password="correct horse battery staple",
            device_id="550e8400-e29b-41d4-a716-446655440000",
        )


def test_secrets_are_masked_in_representations() -> None:
    """Credentials should not appear in ordinary object representations."""

    request = LoginRequest(
        email="user@example.com",
        password="very-secret-password",
        device_id="550e8400-e29b-41d4-a716-446655440000",
    )

    assert "very-secret-password" not in repr(request)


def test_token_response_serializes_credentials_for_api_output() -> None:
    """Token values should be available in JSON responses but masked in repr."""

    now = datetime.now(UTC)
    response = TokenPairResponse(
        access_token="access-secret",
        refresh_token="refresh-secret",
        access_token_expires_at=now,
        refresh_token_expires_at=now,
    )

    assert "access-secret" not in repr(response)
    assert response.model_dump(mode="json")["access_token"] == "**********"
    assert response.access_token.get_secret_value() == "access-secret"
