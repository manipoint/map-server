"""Integration tests for authentication-session management routes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_auth_service, get_current_principal
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.auth import router
from app.api.websocket.connection_manager import ConnectionManager
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.auth.tokens import AccessTokenClaims
from app.database.models.auth_session import AuthSession
from app.database.models.user import User


def create_sessions_app(
    auth_service: MagicMock,
    principal: AuthenticatedPrincipal | None = None,
    connection_manager: MagicMock | None = None,
) -> FastAPI:
    """Create an application containing the protected session routes."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_auth_service] = lambda: auth_service
    application.state.connection_manager = connection_manager or MagicMock(
        spec=ConnectionManager
    )

    if principal is not None:
        application.dependency_overrides[get_current_principal] = lambda: principal

    return application


def create_auth_session(
    *,
    user_id: object,
    current_time: datetime,
    device_id: str,
    device_name: str,
) -> AuthSession:
    """Create a serializable active session for route tests."""

    return AuthSession(
        id=uuid4(),
        user_id=user_id,
        refresh_token_hash=f"hash-{uuid4()}",
        token_family_id=uuid4(),
        device_id=device_id,
        device_name=device_name,
        created_at=current_time,
        last_used_at=current_time,
        expires_at=current_time + timedelta(days=30),
        revoked_at=None,
    )


def create_principal() -> AuthenticatedPrincipal:
    """Create a trusted user and current device session."""

    current_time = datetime.now(UTC).replace(microsecond=0)
    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
        created_at=current_time,
    )
    auth_session = create_auth_session(
        user_id=user.id,
        current_time=current_time,
        device_id="current-device-123456",
        device_name="Current phone",
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


def test_list_sessions_returns_active_devices_and_marks_current_session() -> None:
    """The response should identify only the session used by this request."""

    principal = create_principal()
    other_session = create_auth_session(
        user_id=principal.user.id,
        current_time=principal.auth_session.created_at,
        device_id="other-device-1234567",
        device_name="Tablet",
    )
    auth_service = MagicMock(spec=AuthService)
    auth_service.list_active_sessions = AsyncMock(
        return_value=[principal.auth_session, other_session]
    )
    application = create_sessions_app(auth_service, principal)

    with TestClient(application) as client:
        response = client.get("/api/v1/auth/sessions")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [
        str(principal.auth_session.id),
        str(other_session.id),
    ]
    assert [item["is_current"] for item in response.json()] == [True, False]
    assert "refresh_token_hash" not in response.text
    assert "token_family_id" not in response.text
    auth_service.list_active_sessions.assert_awaited_once_with(
        user_id=principal.user.id
    )


def test_list_sessions_returns_an_empty_list() -> None:
    """An authenticated user with no active sessions should receive an empty list."""

    principal = create_principal()
    auth_service = MagicMock(spec=AuthService)
    auth_service.list_active_sessions = AsyncMock(return_value=[])
    application = create_sessions_app(auth_service, principal)

    with TestClient(application) as client:
        response = client.get("/api/v1/auth/sessions")

    assert response.status_code == 200
    assert response.json() == []


def test_list_sessions_rejects_an_unauthenticated_request() -> None:
    """A missing bearer token should prevent session data disclosure."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock()
    auth_service.list_active_sessions = AsyncMock()
    application = create_sessions_app(auth_service)

    with TestClient(application) as client:
        response = client.get("/api/v1/auth/sessions")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_access_token"
    auth_service.list_active_sessions.assert_not_awaited()


def test_delete_session_revokes_the_selected_user_session() -> None:
    """A selected current or remote device should be revocable by identifier."""

    principal = create_principal()
    selected_session_id = uuid4()
    auth_service = MagicMock(spec=AuthService)
    auth_service.logout = AsyncMock(return_value=True)
    connection_manager = MagicMock(spec=ConnectionManager)
    connection_manager.close_session = AsyncMock(return_value=1)
    application = create_sessions_app(
        auth_service,
        principal,
        connection_manager,
    )

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/auth/sessions/{selected_session_id}")

    assert response.status_code == 204
    assert response.content == b""
    auth_service.logout.assert_awaited_once_with(
        user_id=principal.user.id,
        session_id=selected_session_id,
    )
    connection_manager.close_session.assert_awaited_once_with(selected_session_id)


def test_delete_session_is_idempotent_when_no_owned_session_exists() -> None:
    """An absent or non-owned session should not disclose its existence."""

    principal = create_principal()
    selected_session_id = uuid4()
    auth_service = MagicMock(spec=AuthService)
    auth_service.logout = AsyncMock(return_value=False)
    connection_manager = MagicMock(spec=ConnectionManager)
    connection_manager.close_session = AsyncMock()
    application = create_sessions_app(
        auth_service,
        principal,
        connection_manager,
    )

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/auth/sessions/{selected_session_id}")

    assert response.status_code == 204
    auth_service.logout.assert_awaited_once_with(
        user_id=principal.user.id,
        session_id=selected_session_id,
    )
    connection_manager.close_session.assert_not_awaited()


def test_delete_session_rejects_an_invalid_session_identifier() -> None:
    """FastAPI should reject a malformed UUID before calling the service."""

    principal = create_principal()
    auth_service = MagicMock(spec=AuthService)
    auth_service.logout = AsyncMock()
    connection_manager = MagicMock(spec=ConnectionManager)
    connection_manager.close_session = AsyncMock()
    application = create_sessions_app(
        auth_service,
        principal,
        connection_manager,
    )

    with TestClient(application) as client:
        response = client.delete("/api/v1/auth/sessions/not-a-uuid")

    assert response.status_code == 422
    auth_service.logout.assert_not_awaited()
    connection_manager.close_session.assert_not_awaited()


def test_delete_session_rejects_an_unauthenticated_request() -> None:
    """A missing bearer token should prevent selected-session revocation."""

    selected_session_id = uuid4()
    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock()
    auth_service.logout = AsyncMock()
    connection_manager = MagicMock(spec=ConnectionManager)
    connection_manager.close_session = AsyncMock()
    application = create_sessions_app(
        auth_service,
        connection_manager=connection_manager,
    )

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/auth/sessions/{selected_session_id}")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_access_token"
    auth_service.logout.assert_not_awaited()
    connection_manager.close_session.assert_not_awaited()
