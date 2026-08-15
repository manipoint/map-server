"""Integration tests for the refresh-token endpoint."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_auth_service
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.auth import router
from app.auth.exceptions import (
    AccountNotActiveError,
    AuthenticationError,
    InvalidRefreshTokenError,
    RefreshTokenReuseError,
    SessionRevokedError,
)
from app.auth.service import AuthenticationResult, AuthService
from app.auth.tokens import IssuedAccessToken, IssuedRefreshToken
from app.database.models.auth_session import AuthSession
from app.database.models.user import User


def create_refresh_app(auth_service: MagicMock) -> FastAPI:
    """Create an application containing the refresh route."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_auth_service] = lambda: auth_service
    return application


def create_refresh_result() -> AuthenticationResult:
    """Create replacement credentials returned after rotation."""

    issued_at = datetime.now(UTC).replace(microsecond=0)
    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
        created_at=issued_at,
    )
    return AuthenticationResult(
        user=user,
        auth_session=MagicMock(spec=AuthSession),
        access_token=IssuedAccessToken(
            token="new-signed-access-token",
            expires_at=issued_at + timedelta(minutes=15),
        ),
        refresh_token=IssuedRefreshToken(
            token="new-opaque-refresh-token",
            token_hash="new-stored-refresh-token-hash",
            expires_at=issued_at + timedelta(days=30),
        ),
    )


def test_refresh_returns_replacement_credentials_without_access_token() -> None:
    """A valid refresh credential should rotate without a bearer header."""

    raw_refresh_token = "r" * 64
    result = create_refresh_result()
    auth_service = MagicMock(spec=AuthService)
    auth_service.refresh = AsyncMock(return_value=result)
    application = create_refresh_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            headers={"User-Agent": "TravelAssistant/2.0"},
            json={"refresh_token": raw_refresh_token},
        )

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(result.user.id)
    assert response.json()["tokens"]["access_token"] == ("new-signed-access-token")
    assert response.json()["tokens"]["refresh_token"] == ("new-opaque-refresh-token")
    auth_service.refresh.assert_awaited_once_with(
        refresh_token=raw_refresh_token,
        ip_address=None,
        user_agent="TravelAssistant/2.0",
    )
    assert raw_refresh_token not in response.text
    assert "new-stored-refresh-token-hash" not in response.text


def test_refresh_rejects_invalid_payload_before_service_call() -> None:
    """A short refresh credential should fail request validation."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.refresh = AsyncMock()
    application = create_refresh_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "too-short"},
        )

    assert response.status_code == 422
    auth_service.refresh.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_message"),
    [
        (
            InvalidRefreshTokenError("private invalid-token detail"),
            "invalid_refresh_token",
            "Invalid or expired refresh token",
        ),
        (
            SessionRevokedError("private revoked-session detail"),
            "session_revoked",
            "Authentication session is no longer active",
        ),
        (
            RefreshTokenReuseError("private reuse detail"),
            "refresh_token_reuse",
            "Authentication is required",
        ),
    ],
)
def test_refresh_maps_authentication_failures_to_safe_unauthorized_response(
    error: AuthenticationError,
    expected_code: str,
    expected_message: str,
) -> None:
    """Refresh failures should produce stable 401 responses without leakage."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.refresh = AsyncMock(side_effect=error)
    application = create_refresh_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "r" * 64},
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["message"] == expected_message
    assert str(error) not in response.text


def test_refresh_maps_inactive_account_to_forbidden() -> None:
    """An inactive account should receive a safe forbidden response."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.refresh = AsyncMock(
        side_effect=AccountNotActiveError("private account detail")
    )
    application = create_refresh_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "r" * 64},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_not_active"
    assert response.json()["error"]["message"] == "User account is not active"
    assert "WWW-Authenticate" not in response.headers
    assert "private account detail" not in response.text
