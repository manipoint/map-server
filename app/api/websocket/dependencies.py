"""Authentication dependencies for WebSocket connections."""

from typing import Annotated

from fastapi import Depends, WebSocket, WebSocketException

from app.api.websocket.connection_manager import ConnectionManager
from app.auth.exceptions import AuthenticationError, InvalidAccessTokenError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.config import Settings
from app.database.session import AsyncSessionFactory

WS_UNAUTHORIZED_CODE = 4401


def extract_bearer_token(authorization: str | None) -> str:
    """Extract a bearer token from a WebSocket authorization header."""

    if authorization is None:
        raise InvalidAccessTokenError("Access token is required")

    parts = authorization.split()
    if len(parts) != 2:
        raise InvalidAccessTokenError("Bearer access token is required")

    scheme, token = parts
    if scheme.lower() != "bearer" or not token:
        raise InvalidAccessTokenError("Bearer access token is required")

    return token


async def get_websocket_principal(websocket: WebSocket) -> AuthenticatedPrincipal:
    """Authenticate a WebSocket handshake using an active access token."""
    try:
        access_token = extract_bearer_token(
            websocket.headers.get("authorization"),
        )
        settings: Settings = websocket.app.state.settings
        session_factory: AsyncSessionFactory = websocket.app.state.session_factory
        async with session_factory() as database_session:
            auth_service = AuthService(session=database_session, settings=settings)
            return await auth_service.authenticate_access_token(
                access_token=access_token
            )
    except AuthenticationError as e:
        raise WebSocketException(
            code=WS_UNAUTHORIZED_CODE, reason="Unauthorized"
        ) from e


def get_connection_manager(websocket: WebSocket) -> ConnectionManager:
    """Return the application WebSocket connection manager."""
    return websocket.app.state.connection_manager


WebSocketPrincipal = Annotated[
    AuthenticatedPrincipal,
    Depends(get_websocket_principal),
]
ConnectionManagerDependency = Annotated[
    ConnectionManager, Depends(get_connection_manager)
]
