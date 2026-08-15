"""Integration tests for the current-device logout endpoint."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_auth_service,
    get_current_principal,
)
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.auth import router
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.auth.tokens import AccessTokenClaims
from app.database.models.auth_session import AuthSession
from app.database.models.user import User


def create_logout_app(
    auth_service: MagicMock,
    principal: AuthenticatedPrincipal | None = None,
) -> FastAPI:
    """Create an application containing the protected logout route."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_auth_service] = lambda: auth_service

    if principal is not None:
        application.dependency_overrides[get_current_principal] = lambda: principal

    return application


def create_principal() -> AuthenticatedPrincipal:
    """Create a trusted user and session for protected route tests."""

    current_time = datetime.now(UTC).replace(microsecond=0)
    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
        created_at=current_time,
    )
    auth_session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        refresh_token_hash="stored-refresh-token-hash",
        token_family_id=uuid4(),
        device_id="flutter-device-123456",
        expires_at=current_time + timedelta(days=30),
    )
    return AuthenticatedPrincipal(
        user=user,
        auth_session=auth_session,
        claims=AccessTokenClaims(
            user_id=user.id,
            session_id=auth_session.id,
            token_id=uuid4(),
            issued_at=current_time,
            expires_at=current_time + timedelta(minutes=15),
        ),
    )


@pytest.mark.parametrize("session_revoked", [True, False])
def test_logout_returns_no_content_for_idempotent_result(
    session_revoked: bool,
) -> None:
    """Logout should return 204 whether revocation is new or repeated."""

    principal = create_principal()
    auth_service = MagicMock(spec=AuthService)
    auth_service.logout = AsyncMock(return_value=session_revoked)
    application = create_logout_app(auth_service, principal)

    with TestClient(application) as client:
        response = client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert response.content == b""
    auth_service.logout.assert_awaited_once_with(
        user_id=principal.user.id,
        session_id=principal.auth_session.id,
    )


def test_logout_rejects_missing_bearer_token() -> None:
    """Logout without an authenticated principal should return a safe 401."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock()
    auth_service.logout = AsyncMock()
    application = create_logout_app(auth_service)

    with TestClient(application) as client:
        response = client.post("/api/v1/auth/logout")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_access_token"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    auth_service.authenticate_access_token.assert_not_awaited()
    auth_service.logout.assert_not_awaited()


@pytest.mark.parametrize("revoked_count", [3, 0])
def test_logout_all_returns_no_content_for_idempotent_result(
    revoked_count: int,
) -> None:
    """Logout-all should return 204 with or without active sessions."""

    principal = create_principal()
    auth_service = MagicMock(spec=AuthService)
    auth_service.logout_all = AsyncMock(return_value=revoked_count)
    application = create_logout_app(auth_service, principal)

    with TestClient(application) as client:
        response = client.post("/api/v1/auth/logout-all")

    assert response.status_code == 204
    assert response.content == b""
    auth_service.logout_all.assert_awaited_once_with(
        user_id=principal.user.id,
    )


def test_logout_all_rejects_missing_bearer_token() -> None:
    """Logout-all without an authenticated principal should return 401."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock()
    auth_service.logout_all = AsyncMock()
    application = create_logout_app(auth_service)

    with TestClient(application) as client:
        response = client.post("/api/v1/auth/logout-all")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_access_token"
    auth_service.authenticate_access_token.assert_not_awaited()
    auth_service.logout_all.assert_not_awaited()
