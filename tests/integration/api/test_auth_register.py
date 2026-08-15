"""Integration tests for the registration endpoint."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_auth_service
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.auth import router
from app.auth.exceptions import AuthenticationError, EmailAlreadyRegisteredError
from app.auth.service import AuthenticationResult, AuthService
from app.auth.tokens import IssuedAccessToken, IssuedRefreshToken
from app.database.models.auth_session import AuthSession
from app.database.models.user import User


def create_registration_app(auth_service: MagicMock) -> FastAPI:
    """Create an application containing the registration route."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_auth_service] = lambda: auth_service
    return application


def create_authentication_result() -> AuthenticationResult:
    """Create a complete result returned by the mocked auth service."""

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
            token="signed-access-token",
            expires_at=issued_at + timedelta(minutes=15),
        ),
        refresh_token=IssuedRefreshToken(
            token="opaque-refresh-token",
            token_hash="stored-refresh-token-hash",
            expires_at=issued_at + timedelta(days=30),
        ),
    )


def test_register_returns_user_and_token_pair() -> None:
    """Valid registration data should return a safe authentication response."""

    result = create_authentication_result()
    auth_service = MagicMock(spec=AuthService)
    auth_service.register = AsyncMock(return_value=result)
    application = create_registration_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/register",
            headers={"User-Agent": "TravelAssistant/1.0"},
            json={
                "email": " USER@EXAMPLE.COM ",
                "password": "correct horse battery staple",
                "device_id": "flutter-device-123456",
                "device_name": "  Imran's iPhone  ",
            },
        )

    assert response.status_code == 201
    assert response.json() == {
        "user": {
            "id": str(result.user.id),
            "email": "user@example.com",
            "status": "active",
            "created_at": result.user.created_at.isoformat().replace(
                "+00:00",
                "Z",
            ),
        },
        "tokens": {
            "access_token": "signed-access-token",
            "refresh_token": "opaque-refresh-token",
            "token_type": "bearer",
            "access_token_expires_at": (
                result.access_token.expires_at.isoformat().replace(
                    "+00:00",
                    "Z",
                )
            ),
            "refresh_token_expires_at": (
                result.refresh_token.expires_at.isoformat().replace(
                    "+00:00",
                    "Z",
                )
            ),
        },
    }
    auth_service.register.assert_awaited_once_with(
        email="user@example.com",
        password="correct horse battery staple",
        device_id="flutter-device-123456",
        device_name="Imran's iPhone",
        ip_address=None,
        user_agent="TravelAssistant/1.0",
    )
    assert "password_hash" not in response.text
    assert "stored-refresh-token-hash" not in response.text


def test_register_rejects_invalid_payload_before_service_call() -> None:
    """Invalid registration input should not reach the auth service."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.register = AsyncMock()
    application = create_registration_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "not-an-email",
                "password": "short",
                "device_id": "short-device",
                "device_name": None,
            },
        )

    assert response.status_code == 422
    auth_service.register.assert_not_awaited()


def test_register_maps_duplicate_email_to_conflict() -> None:
    """A duplicate email should use the standard safe conflict response."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.register = AsyncMock(
        side_effect=EmailAlreadyRegisteredError("private database detail")
    )
    application = create_registration_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "user@example.com",
                "password": "correct horse battery staple",
                "device_id": "flutter-device-123456",
                "device_name": None,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_already_registered"
    assert response.json()["error"]["message"] == (
        "An account with this email already exists"
    )
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "private database detail" not in response.text
    assert "WWW-Authenticate" not in response.headers
