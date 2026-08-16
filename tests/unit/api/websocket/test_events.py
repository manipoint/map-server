"""Unit tests for versioned WebSocket event schemas."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.api.websocket.constants import PROTOCOL_VERSION
from app.api.websocket.events import (
    ConnectionPingEvent,
    ConnectionPongEvent,
    ConnectionReadyEvent,
    ConnectionReadyPayload,
    TravelRequestAcceptedEvent,
    TravelRequestAcceptedPayload,
    TravelRequestEvent,
    validate_client_event,
)


def create_ready_payload_data() -> dict[str, object]:
    """Return one valid connection-ready payload dictionary."""

    return {
        "connection_id": str(uuid4()),
        "heartbeat_interval_seconds": 30.0,
        "idle_timeout_seconds": 90.0,
        "max_message_bytes": 65536,
    }


def create_travel_request_event_data() -> dict[str, object]:
    """Return one complete travel-request event dictionary."""

    return {
        "version": 1,
        "type": "travel.request",
        "sent_at": "2026-08-16T12:30:00Z",
        "payload": {
            "client_message_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "message": "Plan a three-day trip to Lahore",
            "locale": "en-PK",
        },
    }


def create_ping_event_data() -> dict[str, object]:
    """Return one complete client heartbeat event dictionary."""

    return {
        "version": 1,
        "type": "connection.ping",
        "sent_at": "2026-08-16T12:30:00Z",
        "payload": {},
    }


def test_connection_ready_event_contains_the_protocol_envelope() -> None:
    """A ready event should include version, type, timestamp, and payload."""

    connection_id = uuid4()
    event = ConnectionReadyEvent(
        payload=ConnectionReadyPayload(
            connection_id=connection_id,
            heartbeat_interval_seconds=30.0,
            idle_timeout_seconds=90.0,
            max_message_bytes=65536,
        )
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
        payload=ConnectionReadyPayload(
            connection_id=connection_id,
            heartbeat_interval_seconds=30.0,
            idle_timeout_seconds=90.0,
            max_message_bytes=65536,
        ),
    )

    assert event.model_dump(mode="json") == {
        "version": 1,
        "sent_at": "2026-08-15T12:30:00Z",
        "type": "connection.ready",
        "payload": {
            "connection_id": str(connection_id),
            "heartbeat_interval_seconds": 30.0,
            "idle_timeout_seconds": 90.0,
            "max_message_bytes": 65536,
        },
    }


def test_connection_ready_event_rejects_an_unsupported_version() -> None:
    """A schema version outside the supported literal should be rejected."""

    with pytest.raises(ValidationError):
        ConnectionReadyEvent.model_validate(
            {
                "version": 2,
                "payload": create_ready_payload_data(),
            }
        )


def test_connection_ready_event_rejects_an_unknown_event_type() -> None:
    """The ready-event schema should not accept another event name."""

    with pytest.raises(ValidationError):
        ConnectionReadyEvent.model_validate(
            {
                "type": "connection.closed",
                "payload": create_ready_payload_data(),
            }
        )


@pytest.mark.parametrize(
    "event_data",
    [
        {
            "payload": create_ready_payload_data(),
            "unexpected": True,
        },
        {
            "payload": {
                **create_ready_payload_data(),
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


@pytest.mark.parametrize(
    "field_name",
    [
        "heartbeat_interval_seconds",
        "idle_timeout_seconds",
        "max_message_bytes",
    ],
)
def test_connection_ready_payload_rejects_nonpositive_limits(
    field_name: str,
) -> None:
    """Advertised runtime limits must always be positive client values."""

    payload = create_ready_payload_data()
    payload[field_name] = 0

    with pytest.raises(ValidationError):
        ConnectionReadyPayload.model_validate(payload)


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


def test_travel_request_accepted_event_generates_the_protocol_envelope() -> None:
    """An acknowledgement should identify the accepted client request."""

    client_message_id = uuid4()
    sent_at = datetime(2026, 8, 16, 12, 45, tzinfo=UTC)
    event = TravelRequestAcceptedEvent(
        sent_at=sent_at,
        payload=TravelRequestAcceptedPayload(
            client_message_id=client_message_id,
        ),
    )

    assert event.model_dump(mode="json") == {
        "version": PROTOCOL_VERSION,
        "sent_at": "2026-08-16T12:45:00Z",
        "type": "travel.request.accepted",
        "payload": {"client_message_id": str(client_message_id)},
    }


def test_travel_request_accepted_payload_parses_a_uuid_string() -> None:
    """Wire-format UUID text should become a typed client-message identifier."""

    client_message_id = uuid4()
    payload = TravelRequestAcceptedPayload.model_validate(
        {"client_message_id": str(client_message_id)}
    )

    assert payload.client_message_id == client_message_id


def test_travel_request_accepted_payload_rejects_an_unknown_field() -> None:
    """Acknowledgements should not expose an extensible accidental contract."""

    with pytest.raises(ValidationError):
        TravelRequestAcceptedPayload.model_validate(
            {
                "client_message_id": str(uuid4()),
                "unexpected": True,
            }
        )


def test_travel_request_accepted_payload_requires_a_client_message_id() -> None:
    """An acknowledgement without correlation metadata should be invalid."""

    with pytest.raises(ValidationError):
        TravelRequestAcceptedPayload.model_validate({})


def test_travel_request_accepted_event_rejects_an_incorrect_type() -> None:
    """The acknowledgement schema should enforce its server event name."""

    with pytest.raises(ValidationError):
        TravelRequestAcceptedEvent.model_validate(
            {
                "type": "travel.request.completed",
                "payload": {"client_message_id": str(uuid4())},
            }
        )


def test_travel_request_event_validates_a_complete_request() -> None:
    """A complete request should retain typed identifiers and client context."""

    event_data = create_travel_request_event_data()
    payload = event_data["payload"]
    assert isinstance(payload, dict)
    payload["message"] = "  Plan a three-day trip to Lahore  "

    event = TravelRequestEvent.model_validate(event_data)

    assert event.version == PROTOCOL_VERSION
    assert event.type == "travel.request"
    assert event.payload.message == "Plan a three-day trip to Lahore"
    assert event.payload.locale == "en-PK"
    assert event.payload.client_message_id == UUID(str(payload["client_message_id"]))
    assert event.payload.conversation_id == UUID(str(payload["conversation_id"]))


def test_travel_request_event_allows_a_new_conversation() -> None:
    """A request may omit conversation ID and use the default locale."""

    event_data = create_travel_request_event_data()
    payload = event_data["payload"]
    assert isinstance(payload, dict)
    payload.pop("conversation_id")
    payload.pop("locale")

    event = TravelRequestEvent.model_validate(event_data)

    assert event.payload.conversation_id is None
    assert event.payload.locale == "en"


@pytest.mark.parametrize("message", ["", "   ", "\n\t"])
def test_travel_request_event_rejects_a_blank_message(message: str) -> None:
    """A request must contain meaningful text after whitespace stripping."""

    event_data = create_travel_request_event_data()
    payload = event_data["payload"]
    assert isinstance(payload, dict)
    payload["message"] = message

    with pytest.raises(ValidationError):
        TravelRequestEvent.model_validate(event_data)


def test_travel_request_event_rejects_a_message_over_the_limit() -> None:
    """A request message may not exceed the configured schema boundary."""

    event_data = create_travel_request_event_data()
    payload = event_data["payload"]
    assert isinstance(payload, dict)
    payload["message"] = "a" * 2001

    with pytest.raises(ValidationError):
        TravelRequestEvent.model_validate(event_data)


@pytest.mark.parametrize("field_name", ["client_message_id", "conversation_id"])
def test_travel_request_event_rejects_an_invalid_identifier(
    field_name: str,
) -> None:
    """Client and conversation identifiers must use valid UUID syntax."""

    event_data = create_travel_request_event_data()
    payload = event_data["payload"]
    assert isinstance(payload, dict)
    payload[field_name] = "not-a-uuid"

    with pytest.raises(ValidationError):
        TravelRequestEvent.model_validate(event_data)


@pytest.mark.parametrize("location", ["envelope", "payload"])
def test_travel_request_event_rejects_an_unknown_field(location: str) -> None:
    """Unknown envelope and request fields should not silently pass validation."""

    event_data = create_travel_request_event_data()
    if location == "envelope":
        event_data["unexpected"] = True
    else:
        payload = event_data["payload"]
        assert isinstance(payload, dict)
        payload["unexpected"] = True

    with pytest.raises(ValidationError):
        TravelRequestEvent.model_validate(event_data)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("version", 2), ("type", "connection.ping")],
)
def test_travel_request_event_rejects_an_invalid_envelope(
    field_name: str,
    value: object,
) -> None:
    """Only the supported protocol version and event type should validate."""

    event_data = create_travel_request_event_data()
    event_data[field_name] = value

    with pytest.raises(ValidationError):
        TravelRequestEvent.model_validate(event_data)


def test_travel_request_event_requires_a_client_message_identifier() -> None:
    """Every request needs an idempotency identifier supplied by the client."""

    event_data = create_travel_request_event_data()
    payload = event_data["payload"]
    assert isinstance(payload, dict)
    payload.pop("client_message_id")

    with pytest.raises(ValidationError):
        TravelRequestEvent.model_validate(event_data)


def test_client_event_parser_routes_a_ping_event() -> None:
    """The discriminator should route a heartbeat to its concrete model."""

    event = validate_client_event(create_ping_event_data())

    assert isinstance(event, ConnectionPingEvent)
    assert event.type == "connection.ping"


def test_client_event_parser_routes_a_travel_request() -> None:
    """The discriminator should route a travel request to its concrete model."""

    event = validate_client_event(create_travel_request_event_data())

    assert isinstance(event, TravelRequestEvent)
    assert event.type == "travel.request"


def test_client_event_parser_rejects_an_unknown_event_type() -> None:
    """A client event outside the supported protocol should fail validation."""

    event_data = create_ping_event_data()
    event_data["type"] = "hotel.search"

    with pytest.raises(ValidationError):
        validate_client_event(event_data)


def test_client_event_parser_rejects_an_invalid_payload() -> None:
    """Routing by type must still validate the selected event payload."""

    event_data = create_travel_request_event_data()
    payload = event_data["payload"]
    assert isinstance(payload, dict)
    payload["message"] = "   "

    with pytest.raises(ValidationError):
        validate_client_event(event_data)


def test_client_event_parser_rejects_an_unsupported_version() -> None:
    """Every routed client event must use the current protocol version."""

    event_data = create_ping_event_data()
    event_data["version"] = 2

    with pytest.raises(ValidationError):
        validate_client_event(event_data)
