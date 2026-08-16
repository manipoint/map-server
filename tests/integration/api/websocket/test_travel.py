"""Integration tests for the authenticated travel WebSocket endpoint."""

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.websocket.connection_manager import ConnectionManager
from app.api.websocket.dependencies import get_websocket_principal
from app.api.websocket.events import ConnectionPongEvent, ConnectionReadyEvent
from app.api.websocket.travel import (
    WS_INVALID_PAYLOAD_CODE,
    WS_POLICY_VIOLATION_CODE,
    WS_UNSUPPORTED_DATA_CODE,
    router,
)
from app.auth.service import AuthenticatedPrincipal


def create_travel_websocket_app() -> FastAPI:
    """Create an application with an authenticated travel socket."""

    application = FastAPI()
    application.include_router(router)
    principal = MagicMock(spec=AuthenticatedPrincipal)
    principal.user.id = uuid4()
    principal.auth_session.id = uuid4()
    application.state.connection_manager = ConnectionManager()
    application.dependency_overrides[get_websocket_principal] = lambda: principal
    return application


def create_ping_event() -> dict[str, object]:
    """Return one valid client heartbeat envelope."""

    return {
        "version": 1,
        "type": "connection.ping",
        "sent_at": "2026-08-15T16:30:00Z",
        "payload": {},
    }


def test_travel_websocket_sends_a_typed_ready_event() -> None:
    """An authenticated connection should receive the protocol-ready envelope."""

    application = create_travel_websocket_app()
    connection_manager = application.state.connection_manager

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            message = websocket.receive_json()
            connection_id = UUID(message["payload"]["connection_id"])

            assert connection_manager.active_connection_count == 1
            assert connection_id in connection_manager._connections

    event = ConnectionReadyEvent.model_validate(message)
    assert event.version == 1
    assert event.type == "connection.ready"
    assert event.sent_at.tzinfo is not None
    assert set(message["payload"]) == {"connection_id"}
    assert "user_id" not in message
    assert "session_id" not in message
    assert connection_manager.active_connection_count == 0
    assert connection_manager._connections == {}
    assert connection_manager._user_connections == {}
    assert connection_manager._session_connections == {}


def test_travel_websocket_creates_a_unique_connection_identifier() -> None:
    """Separate socket handshakes should receive different connection IDs."""

    application = create_travel_websocket_app()
    connection_manager = application.state.connection_manager

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as first_websocket:
            first_message = first_websocket.receive_json()

        with client.websocket_connect("/ws/travel") as second_websocket:
            second_message = second_websocket.receive_json()

    assert (
        first_message["payload"]["connection_id"]
        != (second_message["payload"]["connection_id"])
    )
    assert connection_manager.active_connection_count == 0


def test_travel_websocket_returns_pong_for_a_valid_ping() -> None:
    """A valid client heartbeat should receive a typed server heartbeat."""

    application = create_travel_websocket_app()

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(create_ping_event())
            message = websocket.receive_json()

    event = ConnectionPongEvent.model_validate(message)
    assert event.type == "connection.pong"
    assert event.payload.model_dump() == {}


def test_travel_websocket_supports_repeated_heartbeats() -> None:
    """A pong should not close the connection or prevent another heartbeat."""

    application = create_travel_websocket_app()

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()

            websocket.send_json(create_ping_event())
            first_pong = websocket.receive_json()

            websocket.send_json(create_ping_event())
            second_pong = websocket.receive_json()

    assert first_pong["type"] == "connection.pong"
    assert second_pong["type"] == "connection.pong"


def test_travel_websocket_rejects_binary_frames() -> None:
    """The JSON protocol should reject non-text WebSocket messages."""

    application = create_travel_websocket_app()
    connection_manager = application.state.connection_manager

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_bytes(b"{}")

            with pytest.raises(WebSocketDisconnect) as raised:
                websocket.receive_json()

    assert raised.value.code == WS_UNSUPPORTED_DATA_CODE
    assert raised.value.reason == "Text JSON messages are required"
    assert connection_manager.active_connection_count == 0


def test_travel_websocket_rejects_malformed_json() -> None:
    """Malformed text JSON should use the invalid-payload close code."""

    application = create_travel_websocket_app()
    connection_manager = application.state.connection_manager

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_text("{not-valid-json")

            with pytest.raises(WebSocketDisconnect) as raised:
                websocket.receive_json()

    assert raised.value.code == WS_INVALID_PAYLOAD_CODE
    assert raised.value.reason == "Invalid JSON payload"
    assert connection_manager.active_connection_count == 0


@pytest.mark.parametrize(
    "event",
    [
        {
            "version": 2,
            "type": "connection.ping",
            "sent_at": "2026-08-15T16:30:00Z",
            "payload": {},
        },
        {
            "version": 1,
            "type": "flight.search",
            "sent_at": "2026-08-15T16:30:00Z",
            "payload": {},
        },
        {"type": "connection.ping"},
    ],
)
def test_travel_websocket_rejects_an_invalid_client_event(
    event: dict[str, object],
) -> None:
    """Valid JSON outside the current protocol should close with policy violation."""

    application = create_travel_websocket_app()
    connection_manager = application.state.connection_manager

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(event)

            with pytest.raises(WebSocketDisconnect) as raised:
                websocket.receive_json()

    assert raised.value.code == WS_POLICY_VIOLATION_CODE
    assert raised.value.reason == "Invalid client event"
    assert connection_manager.active_connection_count == 0
