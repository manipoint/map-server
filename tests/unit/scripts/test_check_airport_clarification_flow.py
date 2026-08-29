"""Tests for the public airport-clarification smoke script."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

import scripts.check_airport_clarification_flow as script
from app.api.websocket.events import TravelRequestEvent


def create_event_data() -> dict[str, object]:
    """Return one valid structured airport clarification event."""

    return {
        "version": 1,
        "type": "travel.input.required",
        "sent_at": "2026-08-29T12:00:00Z",
        "payload": {
            "client_message_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "assistant_message_id": str(uuid4()),
            "content": "Select a London airport.",
            "is_duplicate": False,
            "clarification": {
                "type": "airport_selection",
                "requests": [
                    {
                        "field": "origin_airport",
                        "query": "lindon",
                        "status": "selection_required",
                        "question": "Select an airport for lindon.",
                        "options": [
                            {
                                "provider_location_id": "london-city",
                                "iata_code": "LON",
                                "location_type": "city",
                                "name": "London",
                                "city_name": "London",
                                "country_name": "United Kingdom",
                                "country_code": "GB",
                            },
                            {
                                "provider_location_id": "stansted",
                                "iata_code": "STN",
                                "location_type": "airport",
                                "name": "London Stansted Airport",
                                "city_name": "London",
                                "country_name": "United Kingdom",
                                "country_code": "GB",
                            },
                        ],
                    }
                ],
            },
        },
    }


def create_completion_event_data(
    *, conversation_id: object | None = None
) -> dict[str, object]:
    """Return one valid completed flight-search event."""

    return {
        "version": 1,
        "type": "travel.response.completed",
        "sent_at": "2026-08-29T12:01:00Z",
        "payload": {
            "client_message_id": str(uuid4()),
            "conversation_id": str(conversation_id or uuid4()),
            "assistant_message_id": str(uuid4()),
            "content": "LON se LHE ke flight offers available hain.",
            "is_duplicate": False,
            "itinerary_id": None,
        },
    }


def test_validate_airport_clarification_accepts_expected_choice() -> None:
    """A matching field and code should validate the complete event."""

    event = script.validate_airport_clarification(
        event_data=create_event_data(),
        expected_field="origin_airport",
        expected_code=" lon ",
    )

    assert event.type == "travel.input.required"
    assert event.payload.clarification.requests[0].options[0].iata_code == "LON"


def test_validate_airport_clarification_rejects_missing_field() -> None:
    """A clarification for the wrong route endpoint should fail the smoke check."""

    with pytest.raises(RuntimeError, match="expected field"):
        script.validate_airport_clarification(
            event_data=create_event_data(),
            expected_field="destination_airport",
            expected_code="LHE",
        )


def test_validate_airport_clarification_rejects_missing_code() -> None:
    """The smoke check must not pass when the expected airport is absent."""

    with pytest.raises(RuntimeError, match="expected airport code"):
        script.validate_airport_clarification(
            event_data=create_event_data(),
            expected_field="origin_airport",
            expected_code="LHR",
        )


def test_validate_airport_clarification_rejects_wrong_event_type() -> None:
    """A normal completion must not masquerade as structured clarification."""

    event_data = create_event_data()
    event_data["type"] = "travel.response.completed"

    with pytest.raises(ValidationError):
        script.validate_airport_clarification(
            event_data=event_data,
            expected_field="origin_airport",
            expected_code="LON",
        )


def test_validate_airport_selection_completion_accepts_completed_event() -> None:
    """The second turn should accept a typed completed response."""

    event = script.validate_airport_selection_completion(
        event_data=create_completion_event_data()
    )

    assert event.type == "travel.response.completed"
    assert "LON se LHE" in event.payload.content


def test_validate_airport_selection_completion_rejects_clarification_loop() -> None:
    """A repeated clarification should expose the unresolved field."""

    with pytest.raises(RuntimeError, match="clarification loop.*origin_airport"):
        script.validate_airport_selection_completion(event_data=create_event_data())


def test_validate_airport_selection_completion_rejects_failed_response() -> None:
    """A provider or graph failure should not pass as a completed search."""

    with pytest.raises(RuntimeError, match="travel.response.failed"):
        script.validate_airport_selection_completion(
            event_data={
                "type": "travel.response.failed",
                "payload": {"code": "provider_unavailable"},
            }
        )


def test_validate_airport_selection_completion_rejects_wrong_conversation() -> None:
    """The second turn must remain correlated to the clarified conversation."""

    with pytest.raises(RuntimeError, match="unexpected conversation"):
        script.validate_airport_selection_completion(
            event_data=create_completion_event_data(),
            expected_conversation_id=uuid4(),
        )


def test_default_message_exercises_typo_and_roman_urdu_direction() -> None:
    """The live default should retain the regression scenario."""

    assert "lindon se lhaore" in script.DEFAULT_MESSAGE
    assert "one-way flight" in script.DEFAULT_MESSAGE
    assert "LON" in script.DEFAULT_SELECTION_MESSAGE
    assert "LHE" in script.DEFAULT_SELECTION_MESSAGE


def test_build_airport_selection_message_uses_requested_field_and_code() -> None:
    """A CLI choice must not silently fall back to the default LON code."""

    assert (
        script.build_airport_selection_message(
            field="origin_airport",
            code=" stn ",
        )
        == "Origin ke liye STN use karo aur flight search continue karo."
    )
    assert (
        script.build_airport_selection_message(
            field="destination_airport",
            code="lhe",
        )
        == "Destination ke liye LHE use karo aur flight search continue karo."
    )


def test_check_flow_reuses_conversation_with_new_client_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selected code should continue on the same open conversation."""

    clarification_data = create_event_data()
    conversation_id = clarification_data["payload"]["conversation_id"]
    completion_data = create_completion_event_data(conversation_id=conversation_id)
    terminal_events = iter((clarification_data, completion_data))
    received_client_ids = []

    class FakeWebSocket:
        def __init__(self) -> None:
            self.sent_messages: list[str] = []

        async def recv(self) -> str:
            return "ready"

        async def send(self, message: str) -> None:
            self.sent_messages.append(message)

    class FakeConnection:
        def __init__(self, websocket: FakeWebSocket) -> None:
            self.websocket = websocket

        async def __aenter__(self) -> FakeWebSocket:
            return self.websocket

        async def __aexit__(self, *args: object) -> None:
            return None

    websocket = FakeWebSocket()

    async def fake_receive_terminal_event(**kwargs: object) -> dict[str, object]:
        received_client_ids.append(kwargs["client_message_id"])
        return next(terminal_events)

    monkeypatch.setattr(
        script,
        "get_settings",
        lambda: SimpleNamespace(
            log_level="INFO",
            websocket_max_message_bytes=64_000,
        ),
    )
    monkeypatch.setattr(script, "configure_logging", lambda level: None)
    monkeypatch.setattr(
        script,
        "connect",
        lambda *args, **kwargs: FakeConnection(websocket),
    )
    monkeypatch.setattr(
        script,
        "parse_server_event",
        lambda raw_message: {
            "type": "connection.ready",
            "payload": {"heartbeat_interval_seconds": 30},
        },
    )
    monkeypatch.setattr(
        script,
        "receive_terminal_event",
        fake_receive_terminal_event,
    )

    result = asyncio.run(
        script.check_airport_clarification_flow(
            access_token="test-access-token",
        )
    )

    assert len(websocket.sent_messages) == 2
    initial_request = TravelRequestEvent.model_validate_json(websocket.sent_messages[0])
    selection_request = TravelRequestEvent.model_validate_json(
        websocket.sent_messages[1]
    )
    assert initial_request.payload.conversation_id is None
    assert str(selection_request.payload.conversation_id) == conversation_id
    assert selection_request.payload.message == (
        "Origin ke liye LON use karo aur flight search continue karo."
    )
    assert selection_request.payload.client_message_id != (
        initial_request.payload.client_message_id
    )
    assert received_client_ids == [
        initial_request.payload.client_message_id,
        selection_request.payload.client_message_id,
    ]
    assert result.selection_client_message_id == (
        selection_request.payload.client_message_id
    )
    assert result.completion_content == completion_data["payload"]["content"]
