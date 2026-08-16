"""Unit tests for versioned WebSocket event schemas."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.websocket.events import (
    PROTOCOL_VERSION,
    ConnectionPingEvent,
    ConnectionPongEvent,
    ConnectionReadyEvent,
    ConnectionReadyPayload,
)


def test_connection_ready_event_contains_the_protocol_envelope() -> None:
    """A ready event should include version, type, timestamp, and payload."""

    connection_id = uuid4()
    event = ConnectionReadyEvent(
        payload=ConnectionReadyPayload(connection_id=connection_id)
    )

    assert event.version == PROTOCOL_VERSION
    assert event.type == "connection.ready"
    assert event.sent_at.tzinfo is not None
    assert event.sent_at.utcoffset() == UTC.utcoffset(event.sent_at)
    assert event.payload.connection_id == connection_id


def test_connection_ready_event_serializes_for_json_transport() -> None:
    """JSON-mode serialization should convert UUID and datetime values."""

    connection_id = uuid4()
    sent_at = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)
    event = ConnectionReadyEvent(
        sent_at=sent_at,
        payload=ConnectionReadyPayload(connection_id=connection_id),
    )

    assert event.model_dump(mode="json") == {
        "version": 1,
        "sent_at": "2026-08-15T12:30:00Z",
        "type": "connection.ready",
        "payload": {"connection_id": str(connection_id)},
    }


def test_connection_ready_event_rejects_an_unsupported_version() -> None:
    """A schema version outside the supported literal should be rejected."""

    with pytest.raises(ValidationError):
        ConnectionReadyEvent.model_validate(
            {
                "version": 2,
                "payload": {"connection_id": str(uuid4())},
            }
        )


def test_connection_ready_event_rejects_an_unknown_event_type() -> None:
    """The ready-event schema should not accept another event name."""

    with pytest.raises(ValidationError):
        ConnectionReadyEvent.model_validate(
            {
                "type": "connection.closed",
                "payload": {"connection_id": str(uuid4())},
            }
        )


@pytest.mark.parametrize(
    "event_data",
    [
        {
            "payload": {"connection_id": str(uuid4())},
            "unexpected": True,
        },
        {
            "payload": {
                "connection_id": str(uuid4()),
                "unexpected": True,
            }
        },
    ],
)
def test_connection_ready_event_rejects_unknown_fields(
    event_data: dict[str, object],
) -> None:
    """Unknown envelope and payload fields should fail contract validation."""

    with pytest.raises(ValidationError):
        ConnectionReadyEvent.model_validate(event_data)


def test_connection_ping_event_validates_a_complete_client_envelope() -> None:
    """A valid heartbeat should retain its required client-supplied fields."""

    sent_at = datetime(2026, 8, 15, 16, 30, tzinfo=UTC)
    event = ConnectionPingEvent.model_validate(
        {
            "version": 1,
            "type": "connection.ping",
            "sent_at": sent_at.isoformat(),
            "payload": {},
        }
    )

    assert event.version == PROTOCOL_VERSION
    assert event.type == "connection.ping"
    assert event.sent_at == sent_at
    assert event.payload.model_dump() == {}


@pytest.mark.parametrize(
    "missing_field",
    ["version", "type", "sent_at", "payload"],
)
def test_connection_ping_event_requires_every_envelope_field(
    missing_field: str,
) -> None:
    """Client heartbeat fields must be explicit rather than server-defaulted."""

    event_data = {
        "version": 1,
        "type": "connection.ping",
        "sent_at": "2026-08-15T16:30:00Z",
        "payload": {},
    }
    event_data.pop(missing_field)

    with pytest.raises(ValidationError):
        ConnectionPingEvent.model_validate(event_data)


@pytest.mark.parametrize(
    "event_data",
    [
        {
            "version": 2,
            "type": "connection.ping",
            "sent_at": "2026-08-15T16:30:00Z",
            "payload": {},
        },
        {
            "version": 1,
            "type": "connection.pong",
            "sent_at": "2026-08-15T16:30:00Z",
            "payload": {},
        },
        {
            "version": 1,
            "type": "connection.ping",
            "sent_at": "2026-08-15T16:30:00",
            "payload": {},
        },
        {
            "version": 1,
            "type": "connection.ping",
            "sent_at": "2026-08-15T16:30:00Z",
            "payload": {"unexpected": True},
        },
    ],
)
def test_connection_ping_event_rejects_an_invalid_contract(
    event_data: dict[str, object],
) -> None:
    """Unsupported versions, types, times, and payload fields should fail."""

    with pytest.raises(ValidationError):
        ConnectionPingEvent.model_validate(event_data)


def test_connection_pong_event_generates_a_server_envelope() -> None:
    """The server should generate pong defaults ready for JSON transport."""

    event = ConnectionPongEvent()
    serialized = event.model_dump(mode="json")

    assert serialized["version"] == PROTOCOL_VERSION
    assert serialized["type"] == "connection.pong"
    assert serialized["sent_at"].endswith("Z")
    assert serialized["payload"] == {}
