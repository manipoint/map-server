"""Unit tests for WebSocket authentication helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.websocket.connection_manager import ConnectionManager
from app.api.websocket.dependencies import (
    create_travel_response_service,
    extract_bearer_token,
    get_connection_manager,
    get_websocket_session_factory,
    get_websocket_settings,
)
from app.auth.exceptions import InvalidAccessTokenError
from app.config import Settings


@pytest.mark.parametrize(
    ("authorization", "expected_token"),
    [
        ("Bearer signed-token", "signed-token"),
        ("bearer signed-token", "signed-token"),
        ("  Bearer   signed-token  ", "signed-token"),
    ],
)
def test_extract_bearer_token_accepts_a_valid_header(
    authorization: str,
    expected_token: str,
) -> None:
    """Bearer scheme matching should be case-insensitive and whitespace-safe."""

    assert extract_bearer_token(authorization) == expected_token


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Basic signed-token",
        "Bearer signed-token unexpected-value",
    ],
)
def test_extract_bearer_token_rejects_a_malformed_header(
    authorization: str | None,
) -> None:
    """Malformed authorization data must not reach token verification."""

    with pytest.raises(InvalidAccessTokenError):
        extract_bearer_token(authorization)


def test_get_connection_manager_returns_the_application_resource() -> None:
    """The dependency should expose the manager owned by this application."""

    connection_manager = ConnectionManager()
    websocket = MagicMock(spec=WebSocket)
    websocket.app = SimpleNamespace(
        state=SimpleNamespace(connection_manager=connection_manager)
    )

    assert get_connection_manager(websocket) is connection_manager


def test_get_websocket_settings_returns_the_application_resource() -> None:
    """The dependency should expose settings owned by this application."""

    settings = MagicMock(spec=Settings)
    websocket = MagicMock(spec=WebSocket)
    websocket.app = SimpleNamespace(state=SimpleNamespace(settings=settings))

    assert get_websocket_settings(websocket) is settings


def test_get_websocket_session_factory_returns_the_application_resource() -> None:
    """The dependency should expose the shared async-session factory."""

    session_factory = MagicMock()
    websocket = MagicMock(spec=WebSocket)
    websocket.app = SimpleNamespace(
        state=SimpleNamespace(session_factory=session_factory)
    )

    assert get_websocket_session_factory(websocket) is session_factory


def test_create_travel_response_service_shares_the_message_session() -> None:
    """Conversation and itinerary writes should use one message-scoped session."""

    settings = MagicMock(spec=Settings)
    settings.conversation_history_message_limit = 20
    settings.assistant_run_lease_seconds = 120
    settings.travel_response_timeout_seconds = 75.0
    settings.max_model_attempts = 3
    graph = object()
    websocket = MagicMock(spec=WebSocket)
    websocket.app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            travel_graph=graph,
        )
    )
    database_session = AsyncMock(spec=AsyncSession)

    service = create_travel_response_service(
        websocket=websocket,
        database_session=database_session,
    )

    assert service.graph is graph
    assert service.processing.session is database_session
    assert service.itineraries.session is database_session
