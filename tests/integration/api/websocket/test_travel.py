"""Integration tests for the authenticated travel WebSocket endpoint."""

from json import dumps
from time import sleep
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.websocket.connection_manager import ConnectionManager
from app.api.websocket.constants import (
    WS_IDLE_TIMEOUT_CODE,
    WS_IDLE_TIMEOUT_REASON,
    WS_INVALID_PAYLOAD_CODE,
    WS_INVALID_PAYLOAD_REASON,
    WS_MESSAGE_TOO_LARGE_CODE,
    WS_MESSAGE_TOO_LARGE_REASON,
    WS_POLICY_VIOLATION_CODE,
    WS_POLICY_VIOLATION_REASON,
    WS_UNSUPPORTED_DATA_CODE,
    WS_UNSUPPORTED_DATA_REASON,
)
from app.api.websocket.dependencies import get_websocket_principal
from app.api.websocket.events import (
    ConnectionPongEvent,
    ConnectionReadyEvent,
    TravelRequestAcceptedEvent,
)
from app.api.websocket.travel import router
from app.auth.service import AuthenticatedPrincipal
from app.config import Settings


def create_travel_websocket_app(
    *,
    idle_timeout_seconds: float = 75.0,
    max_message_bytes: int = 32768,
) -> FastAPI:
    """Create an application with an authenticated travel socket."""

    application = FastAPI()
    application.include_router(router)
    principal = MagicMock(spec=AuthenticatedPrincipal)
    principal.user.id = uuid4()
    principal.auth_session.id = uuid4()
    settings = MagicMock(spec=Settings)
    settings.websocket_heartbeat_interval_seconds = 20.0
    settings.websocket_idle_timeout_seconds = idle_timeout_seconds
    settings.websocket_max_message_bytes = max_message_bytes
    application.state.connection_manager = ConnectionManager()
    application.state.settings = settings
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


def create_travel_request_event(
    *,
    client_message_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> dict[str, object]:
    """Return one valid travel request envelope."""

    payload: dict[str, object] = {
        "client_message_id": str(client_message_id or uuid4()),
        "message": "Plan a three-day trip to Lahore",
        "locale": "en-PK",
    }
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    return {
        "version": 1,
        "type": "travel.request",
        "sent_at": "2026-08-16T12:30:00Z",
        "payload": payload,
    }


def serialize_ping_event() -> str:
    """Serialize a valid heartbeat without optional JSON whitespace."""

    return dumps(create_ping_event(), separators=(",", ":"))


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
    assert set(message["payload"]) == {
        "connection_id",
        "heartbeat_interval_seconds",
        "idle_timeout_seconds",
        "max_message_bytes",
    }
    assert message["payload"]["heartbeat_interval_seconds"] == 20.0
    assert message["payload"]["idle_timeout_seconds"] == 75.0
    assert message["payload"]["max_message_bytes"] == 32768
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


def test_travel_websocket_acknowledges_a_valid_travel_request() -> None:
    """A valid request should receive a typed correlation acknowledgement."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(
                create_travel_request_event(
                    client_message_id=client_message_id,
                )
            )
            message = websocket.receive_json()

    event = TravelRequestAcceptedEvent.model_validate(message)
    assert event.payload.client_message_id == client_message_id
    assert event.sent_at.tzinfo is not None
    assert set(message["payload"]) == {"client_message_id"}


def test_travel_websocket_correlates_repeated_travel_requests() -> None:
    """Sequential requests should each echo their own client-message ID."""

    application = create_travel_websocket_app()
    first_message_id = uuid4()
    second_message_id = uuid4()

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()

            websocket.send_json(
                create_travel_request_event(
                    client_message_id=first_message_id,
                    conversation_id=uuid4(),
                )
            )
            first_acknowledgement = websocket.receive_json()

            websocket.send_json(
                create_travel_request_event(
                    client_message_id=second_message_id,
                )
            )
            second_acknowledgement = websocket.receive_json()

    assert first_acknowledgement["payload"]["client_message_id"] == str(
        first_message_id
    )
    assert second_acknowledgement["payload"]["client_message_id"] == str(
        second_message_id
    )


def test_travel_websocket_keeps_heartbeat_routing_after_a_travel_request() -> None:
    """Acknowledging a request should leave the connection ready for heartbeats."""

    application = create_travel_websocket_app()

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(create_travel_request_event())
            acknowledgement = websocket.receive_json()

            websocket.send_json(create_ping_event())
            pong = websocket.receive_json()

    assert acknowledgement["type"] == "travel.request.accepted"
    assert pong["type"] == "connection.pong"


def test_travel_websocket_closes_an_idle_connection() -> None:
    """A connection without inbound activity should expire and unregister."""

    application = create_travel_websocket_app(idle_timeout_seconds=0.01)
    connection_manager = application.state.connection_manager

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()

            with pytest.raises(WebSocketDisconnect) as raised:
                websocket.receive_json()

    assert raised.value.code == WS_IDLE_TIMEOUT_CODE
    assert raised.value.reason == WS_IDLE_TIMEOUT_REASON
    assert connection_manager.active_connection_count == 0


def test_travel_websocket_activity_resets_the_idle_timeout() -> None:
    """Each valid heartbeat should begin a fresh idle-timeout period."""

    application = create_travel_websocket_app(idle_timeout_seconds=0.3)

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()

            sleep(0.2)
            websocket.send_json(create_ping_event())
            first_pong = websocket.receive_json()

            sleep(0.2)
            websocket.send_json(create_ping_event())
            second_pong = websocket.receive_json()

    assert first_pong["type"] == "connection.pong"
    assert second_pong["type"] == "connection.pong"


def test_travel_websocket_accepts_a_message_at_the_size_limit() -> None:
    """The configured maximum should be inclusive rather than off by one."""

    text = serialize_ping_event()
    application = create_travel_websocket_app(
        max_message_bytes=len(text.encode("utf-8")),
    )

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_text(text)
            message = websocket.receive_json()

    assert message["type"] == "connection.pong"


def test_travel_websocket_rejects_a_message_over_the_size_limit() -> None:
    """A text frame one byte over the limit should close and unregister."""

    text = serialize_ping_event()
    application = create_travel_websocket_app(
        max_message_bytes=len(text.encode("utf-8")) - 1,
    )
    connection_manager = application.state.connection_manager

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_text(text)

            with pytest.raises(WebSocketDisconnect) as raised:
                websocket.receive_json()

    assert raised.value.code == WS_MESSAGE_TOO_LARGE_CODE
    assert raised.value.reason == WS_MESSAGE_TOO_LARGE_REASON
    assert connection_manager.active_connection_count == 0


def test_travel_websocket_measures_message_size_in_utf8_bytes() -> None:
    """Multibyte text should be limited by wire bytes, not Python characters."""

    text = dumps("🌍", ensure_ascii=False)
    application = create_travel_websocket_app(max_message_bytes=len(text))

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_text(text)

            with pytest.raises(WebSocketDisconnect) as raised:
                websocket.receive_json()

    assert len(text.encode("utf-8")) > len(text)
    assert raised.value.code == WS_MESSAGE_TOO_LARGE_CODE


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
    assert raised.value.reason == WS_UNSUPPORTED_DATA_REASON
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
    assert raised.value.reason == WS_INVALID_PAYLOAD_REASON
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
        {
            "version": 1,
            "type": "travel.request",
            "sent_at": "2026-08-16T12:30:00Z",
            "payload": {
                "client_message_id": str(uuid4()),
                "message": "   ",
            },
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
    assert raised.value.reason == WS_POLICY_VIOLATION_REASON
    assert connection_manager.active_connection_count == 0
