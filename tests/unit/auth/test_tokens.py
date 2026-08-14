"""Tests for access-token and refresh-token utilities."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.auth.exceptions import InvalidAccessTokenError
from app.auth.tokens import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_refresh_token,
)
from app.config import Settings


def create_token_settings() -> Settings:
    """Create deterministic settings for token tests."""

    return Settings(
        _env_file=None,
        database_connection_mode="url",
        database_url=SecretStr("postgresql+asyncpg://user:test@localhost/test"),
        jwt_signing_key=SecretStr("test-signing-key-at-least-32-characters"),
        refresh_token_hash_key=SecretStr(
            "test-refresh-hash-key-at-least-32-characters"
        ),
        access_token_ttl_minutes=15,
        refresh_token_ttl_days=30,
    )


def test_access_token_round_trip_preserves_trusted_claims() -> None:
    """A newly issued access token should decode to its original identity."""

    settings = create_token_settings()
    user_id = uuid4()
    session_id = uuid4()
    issued_at = datetime.now(UTC).replace(microsecond=0)

    issued_token = create_access_token(
        user_id=user_id,
        session_id=session_id,
        settings=settings,
        issued_at=issued_at,
    )
    claims = decode_access_token(issued_token.token, settings)

    assert claims.user_id == user_id
    assert claims.session_id == session_id
    assert claims.issued_at == issued_at
    assert claims.expires_at == issued_token.expires_at
    assert claims.token_id is not None


def test_access_token_rejects_wrong_signing_key() -> None:
    """A token signed by another key should not be trusted."""

    settings = create_token_settings()
    issued_token = create_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
    )
    wrong_settings = settings.model_copy(
        update={
            "jwt_signing_key": SecretStr(
                "different-test-signing-key-at-least-32-characters"
            )
        }
    )

    with pytest.raises(InvalidAccessTokenError, match="Invalid access token"):
        decode_access_token(issued_token.token, wrong_settings)


def test_access_token_rejects_wrong_audience() -> None:
    """A token issued for another client should not be trusted."""

    settings = create_token_settings()
    issued_token = create_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
    )
    wrong_settings = settings.model_copy(update={"jwt_audience": "another-client"})

    with pytest.raises(InvalidAccessTokenError, match="Invalid access token"):
        decode_access_token(issued_token.token, wrong_settings)


def test_access_token_rejects_expired_token() -> None:
    """An access token should become invalid after its expiry time."""

    settings = create_token_settings()
    issued_token = create_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
        issued_at=datetime.now(UTC) - timedelta(minutes=16),
    )

    with pytest.raises(InvalidAccessTokenError, match="Invalid access token"):
        decode_access_token(issued_token.token, settings)


def test_refresh_tokens_are_unique_and_have_expected_expiry() -> None:
    """Each refresh credential should be random and long-lived."""

    settings = create_token_settings()
    issued_at = datetime.now(UTC).replace(microsecond=0)

    first = create_refresh_token(settings, issued_at=issued_at)
    second = create_refresh_token(settings, issued_at=issued_at)

    assert first.token != second.token
    assert first.token_hash != second.token_hash
    assert len(first.token) >= 64
    assert first.expires_at == issued_at + timedelta(days=30)


def test_refresh_token_hash_is_deterministic() -> None:
    """The same refresh token and key should produce one lookup hash."""

    settings = create_token_settings()

    first_hash = hash_refresh_token("refresh-token-value", settings)
    second_hash = hash_refresh_token("refresh-token-value", settings)

    assert first_hash == second_hash
    assert len(first_hash) == 64
    assert "refresh-token-value" not in first_hash


def test_issued_token_representations_hide_credentials() -> None:
    """Token dataclasses should not expose credentials in ordinary repr output."""

    settings = create_token_settings()
    access_token = create_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
    )
    refresh_token = create_refresh_token(settings)

    assert access_token.token not in repr(access_token)
    assert refresh_token.token not in repr(refresh_token)
    assert refresh_token.token_hash not in repr(refresh_token)
