"""FastAPI integration tests for WebSocket authentication."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.websocket import dependencies
from app.api.websocket.dependencies import (
    WS_UNAUTHORIZED_CODE,
    WebSocketPrincipal,
)
from app.auth.exceptions import SessionRevokedError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.config import Settings


class SessionContext:
    """Record entry and exit for a temporary database session."""

    def __init__(self, database_session: MagicMock) -> None:
        self.database_session = database_session
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> MagicMock:
        self.entered = True
        return self.database_session

    async def __aexit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        self.exited = True


def create_websocket_app(
    monkeypatch: pytest.MonkeyPatch,
    auth_service: MagicMock,
) -> tuple[FastAPI, SessionContext]:
    """Create an application with one authenticated WebSocket endpoint."""

    application = FastAPI()
    application.state.settings = MagicMock(spec=Settings)
    database_session = MagicMock()
    session_context = SessionContext(database_session)
    application.state.session_factory = lambda: session_context

    def create_auth_service(*, session: object, settings: object) -> MagicMock:
        assert session is database_session
        assert settings is application.state.settings
        return auth_service

    monkeypatch.setattr(dependencies, "AuthService", create_auth_service)

    @application.websocket("/ws")
    async def protected_websocket(
        websocket: WebSocket,
        principal: WebSocketPrincipal,
    ) -> None:
        await websocket.accept()
        await websocket.send_json({"user_id": str(principal.user.id)})
        await websocket.close()

    return application, session_context


def test_websocket_authentication_returns_the_authenticated_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid bearer token should authenticate before socket acceptance."""

    principal = MagicMock(spec=AuthenticatedPrincipal)
    principal.user.id = uuid4()
    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock(return_value=principal)
    application, session_context = create_websocket_app(monkeypatch, auth_service)

    with TestClient(application) as client:
        with client.websocket_connect(
            "/ws",
            headers={"Authorization": "Bearer valid-access-token"},
        ) as websocket:
            message = websocket.receive_json()

    assert message == {"user_id": str(principal.user.id)}
    auth_service.authenticate_access_token.assert_awaited_once_with(
        access_token="valid-access-token"
    )
    assert session_context.entered is True
    assert session_context.exited is True


def test_websocket_authentication_rejects_a_missing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing header should close safely without opening the database."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock()
    application, session_context = create_websocket_app(monkeypatch, auth_service)

    with TestClient(application) as client:
        with pytest.raises(WebSocketDisconnect) as raised:
            with client.websocket_connect("/ws"):
                pass

    assert raised.value.code == WS_UNAUTHORIZED_CODE
    assert raised.value.reason == "Unauthorized"
    auth_service.authenticate_access_token.assert_not_awaited()
    assert session_context.entered is False


def test_websocket_authentication_hides_session_failure_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revoked-session details should be replaced by a safe close reason."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock(
        side_effect=SessionRevokedError("private revocation detail")
    )
    application, session_context = create_websocket_app(monkeypatch, auth_service)

    with TestClient(application) as client:
        with pytest.raises(WebSocketDisconnect) as raised:
            with client.websocket_connect(
                "/ws",
                headers={"Authorization": "Bearer revoked-access-token"},
            ):
                pass

    assert raised.value.code == WS_UNAUTHORIZED_CODE
    assert raised.value.reason == "Unauthorized"
    assert "private revocation detail" not in raised.value.reason
    assert session_context.entered is True
    assert session_context.exited is True
