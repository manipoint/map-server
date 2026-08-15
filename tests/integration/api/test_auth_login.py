"""Integration tests for the login endpoint."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_auth_service
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.auth import router
from app.auth.exceptions import (
    AccountNotActiveError,
    AuthenticationError,
    InvalidCredentialsError,
)
from app.auth.service import AuthenticationResult, AuthService
from app.auth.tokens import IssuedAccessToken, IssuedRefreshToken
from app.database.models.auth_session import AuthSession
from app.database.models.user import User


def create_login_app(auth_service: MagicMock) -> FastAPI:
    """Create an application containing the login route."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_auth_service] = lambda: auth_service
    return application


def create_login_result() -> AuthenticationResult:
    """Create credentials returned by a successful mocked login."""

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


def test_login_returns_user_and_token_pair() -> None:
    """Valid credentials should return the public authentication response."""

    result = create_login_result()
    auth_service = MagicMock(spec=AuthService)
    auth_service.login = AsyncMock(return_value=result)
    application = create_login_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"User-Agent": "TravelAssistant/1.0"},
            json={
                "email": " USER@EXAMPLE.COM ",
                "password": "correct password",
                "device_id": "flutter-device-123456",
                "device_name": "  Imran's iPhone  ",
            },
        )

    assert response.status_code == 200
    response_body = response.json()
    assert response_body["user"] == {
        "id": str(result.user.id),
        "email": "user@example.com",
        "status": "active",
        "created_at": result.user.created_at.isoformat().replace(
            "+00:00",
            "Z",
        ),
    }
    assert response_body["tokens"]["access_token"] == "signed-access-token"
    assert response_body["tokens"]["refresh_token"] == "opaque-refresh-token"
    assert response_body["tokens"]["token_type"] == "bearer"
    auth_service.login.assert_awaited_once_with(
        email="user@example.com",
        password="correct password",
        device_id="flutter-device-123456",
        device_name="Imran's iPhone",
        ip_address=None,
        user_agent="TravelAssistant/1.0",
    )
    assert "password_hash" not in response.text
    assert "stored-refresh-token-hash" not in response.text


def test_login_rejects_invalid_payload_before_service_call() -> None:
    """Invalid login input should not reach the authentication service."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.login = AsyncMock()
    application = create_login_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "not-an-email",
                "password": "",
                "device_id": "short-device",
                "device_name": None,
            },
        )

    assert response.status_code == 422
    auth_service.login.assert_not_awaited()


def test_login_maps_invalid_credentials_to_unauthorized() -> None:
    """Unknown email and wrong password should share the safe 401 response."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.login = AsyncMock(
        side_effect=InvalidCredentialsError("private credential detail")
    )
    application = create_login_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "user@example.com",
                "password": "wrong password",
                "device_id": "flutter-device-123456",
                "device_name": None,
            },
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert response.json()["error"]["message"] == "Invalid email or password"
    assert "private credential detail" not in response.text


def test_login_maps_inactive_account_to_forbidden() -> None:
    """Valid credentials for an inactive account should return a safe 403."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.login = AsyncMock(
        side_effect=AccountNotActiveError("private account detail")
    )
    application = create_login_app(auth_service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "user@example.com",
                "password": "correct password",
                "device_id": "flutter-device-123456",
                "device_name": None,
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_not_active"
    assert response.json()["error"]["message"] == "User account is not active"
    assert "WWW-Authenticate" not in response.headers
    assert "private account detail" not in response.text
