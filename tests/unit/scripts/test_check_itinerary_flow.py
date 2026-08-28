"""Tests for the authenticated itinerary WebSocket smoke script."""

import asyncio
from json import dumps
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import scripts.check_itinerary_flow as script


def test_build_websocket_url_supports_local_and_tls_servers() -> None:
    """HTTP schemes and optional base paths should map predictably."""

    assert script.build_websocket_url("http://127.0.0.1:8000") == (
        "ws://127.0.0.1:8000/ws/travel"
    )
    assert script.build_websocket_url("https://example.com/backend/") == (
        "wss://example.com/backend/ws/travel"
    )


def test_build_websocket_url_rejects_unsafe_or_relative_input() -> None:
    """Only absolute HTTP application URLs should be accepted."""

    with pytest.raises(ValueError, match="absolute HTTP or HTTPS"):
        script.build_websocket_url("127.0.0.1:8000")


def test_require_access_token_reads_environment_without_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command should support shell-injected credentials."""

    monkeypatch.setenv("TRAVEL_ACCESS_TOKEN", " secret-token ")

    assert script.require_access_token() == "secret-token"


def test_require_access_token_rejects_a_missing_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No network request should start without explicit authentication."""

    monkeypatch.delenv("TRAVEL_ACCESS_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TRAVEL_ACCESS_TOKEN is required"):
        script.require_access_token()


def test_default_message_supplies_bounded_smoke_preferences() -> None:
    """The live check should not pause for flight or hotel clarification."""

    assert "one adult" in script.DEFAULT_MESSAGE
    assert "does not request flight or hotel searches" in script.DEFAULT_MESSAGE
    assert "search_places at most once" in script.DEFAULT_MESSAGE
    assert "submit_itinerary exactly once" in script.DEFAULT_MESSAGE


def test_receive_terminal_event_heartbeats_and_correlates_response() -> None:
    """Unrelated terminal events must not satisfy the current request."""

    client_message_id = uuid4()
    websocket = MagicMock()
    websocket.recv = AsyncMock(
        side_effect=[
            TimeoutError,
            dumps(
                {
                    "type": "travel.response.completed",
                    "payload": {
                        "client_message_id": str(uuid4()),
                        "itinerary_id": str(uuid4()),
                    },
                }
            ),
            dumps(
                {
                    "type": "travel.response.completed",
                    "payload": {
                        "client_message_id": str(client_message_id),
                        "itinerary_id": str(uuid4()),
                    },
                }
            ),
        ]
    )
    websocket.send = AsyncMock()

    event = asyncio.run(
        script.receive_terminal_event(
            websocket=websocket,
            client_message_id=client_message_id,
            heartbeat_interval_seconds=0.001,
            total_timeout_seconds=2.0,
        )
    )

    assert event["payload"]["client_message_id"] == str(client_message_id)
    heartbeat = script.parse_server_event(websocket.send.await_args.args[0])
    assert heartbeat["type"] == "connection.ping"


def test_parse_arguments_returns_typed_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI input should be converted before any live request begins."""

    trip_id = uuid4()
    conversation_id = uuid4()
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_itinerary_flow",
            str(trip_id),
            "--conversation-id",
            str(conversation_id),
            "--timeout",
            "45",
        ],
    )

    arguments = script.parse_arguments()

    assert arguments.trip_id == trip_id
    assert arguments.conversation_id == conversation_id
    assert arguments.timeout == 45.0
