"""Integration tests for the authenticated travel WebSocket endpoint."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from json import dumps
from threading import Event
from time import sleep
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, WebSocketException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from starlette.websockets import WebSocketDisconnect

from app.api.websocket import travel
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
    TravelInputRequiredEvent,
    TravelRequestAcceptedEvent,
    TravelRequestRejectedEvent,
    TravelResponseCompletedEvent,
    TravelResponseFailedEvent,
    TravelResponseProcessingEvent,
)
from app.api.websocket.travel import router
from app.auth.service import AuthenticatedPrincipal
from app.config import Settings
from app.database.models.conversation import Conversation
from app.database.models.message import Message
from app.domain.assistant_content import AssistantRichContent
from app.domain.clarifications import AirportInputRequest, TravelClarification
from app.domain.enums import TravelResponseErrorCode
from app.domain.errors import (
    ClientMessageConflictError,
    ConversationNotFoundError,
    TripNotFoundError,
)
from app.graph.subgraphs.model_gateway import ModelGatewayError
from app.providers.airports.schemas import AirportOption
from app.services.conversation_service import AcceptedTravelRequest
from app.services.travel_response_service import TravelResponseResult


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
    settings.websocket_auth_check_seconds = 15
    settings.websocket_max_pending_requests = 4
    principal.claims.expires_at = datetime.now(UTC) + timedelta(hours=1)
    application.state.test_principal = principal
    application.state.connection_manager = ConnectionManager()
    application.state.settings = settings
    application.state.session_factory = MagicMock()
    application.dependency_overrides[get_websocket_principal] = lambda: principal
    return application


def create_accepted_request(
    *,
    conversation_id: UUID | None = None,
    is_duplicate: bool = False,
    trip_id: UUID | None = None,
) -> AcceptedTravelRequest:
    """Return a persisted-request result for endpoint isolation."""

    conversation = MagicMock(spec=Conversation)
    conversation.id = conversation_id or uuid4()
    user_message = MagicMock(spec=Message)
    user_message.trip_id = trip_id
    return AcceptedTravelRequest(
        conversation=conversation,
        user_message=user_message,
        trip=None,
        is_duplicate=is_duplicate,
    )


@pytest.fixture(autouse=True)
def mock_travel_response_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep socket protocol tests independent from graph and database work."""

    async def authenticate(websocket):
        return websocket.app.state.test_principal

    monkeypatch.setattr(travel, "get_websocket_principal", authenticate)
    monkeypatch.setattr(
        travel,
        "generate_travel_response",
        AsyncMock(
            return_value=TravelResponseResult(
                message=None,
                is_cached=False,
                is_processing=True,
                error_code=None,
            )
        ),
    )


def create_ping_event() -> dict[str, object]:
    """Return one valid client heartbeat envelope."""

    return {
        "version": 1,
        "type": "connection.ping",
        "sent_at": "2026-08-15T16:30:00Z",
        "payload": {},
    }


def test_revoked_session_is_checked_before_persisting_a_request(monkeypatch):
    application = create_travel_websocket_app()
    persist = AsyncMock()
    monkeypatch.setattr(travel, "persist_travel_request", persist)
    monkeypatch.setattr(
        travel,
        "get_websocket_principal",
        AsyncMock(side_effect=WebSocketException(code=4401, reason="Session revoked")),
    )
    with (
        TestClient(application) as client,
        client.websocket_connect("/ws/travel") as websocket,
    ):
        websocket.receive_json()
        websocket.send_json(create_travel_request_event())
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_json()
        assert caught.value.code == 4401
    persist.assert_not_awaited()


def test_token_expiry_closes_an_idle_socket_before_poll_interval(monkeypatch):
    application = create_travel_websocket_app()
    application.state.test_principal.claims.expires_at = datetime.now(UTC) + timedelta(
        seconds=0.03
    )
    monkeypatch.setattr(
        travel,
        "get_websocket_principal",
        AsyncMock(side_effect=WebSocketException(code=4401, reason="Token expired")),
    )
    with (
        TestClient(application) as client,
        client.websocket_connect("/ws/travel") as websocket,
    ):
        websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_json()
        assert caught.value.code == 4401


@pytest.mark.parametrize(
    "error,close_code",
    [
        (WebSocketException(code=4401, reason="Session revoked"), 4401),
        (TimeoutError("PRIVATE database details"), 1011),
    ],
)
def test_cross_worker_revocation_cancels_in_flight_generation(
    monkeypatch, error, close_code
):
    application = create_travel_websocket_app()
    application.state.settings.websocket_auth_check_seconds = 0.01
    started, cancelled, revoked = Event(), Event(), Event()

    async def authenticate(websocket):
        if revoked.is_set():
            raise error
        return application.state.test_principal

    async def generate(**kwargs):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    monkeypatch.setattr(travel, "get_websocket_principal", authenticate)
    monkeypatch.setattr(travel, "generate_travel_response", generate)
    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        AsyncMock(return_value=create_accepted_request()),
    )
    with (
        TestClient(application) as client,
        client.websocket_connect("/ws/travel") as websocket,
    ):
        websocket.receive_json()
        websocket.send_json(create_travel_request_event())
        assert websocket.receive_json()["type"] == "travel.request.accepted"
        assert started.wait(1)
        revoked.set()
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_json()
        assert caught.value.code == close_code
    assert cancelled.wait(1)
    assert application.state.connection_manager.active_connection_count == 0


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("PRIVATE connection details"),
        OSError("PRIVATE network details"),
        OperationalError("PRIVATE SQL", {}, Exception("PRIVATE credentials")),
    ],
)
@pytest.mark.parametrize("phase", ["before_persistence", "before_generation"])
def test_database_validation_failure_closes_1011_without_private_logs(
    monkeypatch, caplog, error, phase
):
    application = create_travel_websocket_app()
    persist = AsyncMock(return_value=create_accepted_request())
    generate = AsyncMock()
    checks = 0

    async def authenticate(websocket):
        nonlocal checks
        checks += 1
        if phase == "before_persistence" or checks > 1:
            raise error
        return application.state.test_principal

    monkeypatch.setattr(travel, "get_websocket_principal", authenticate)
    monkeypatch.setattr(travel, "persist_travel_request", persist)
    monkeypatch.setattr(travel, "generate_travel_response", generate)
    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(create_travel_request_event())
            if phase == "before_generation":
                assert websocket.receive_json()["type"] == "travel.request.accepted"
            with pytest.raises(WebSocketDisconnect) as caught:
                websocket.receive_json()
            assert caught.value.code == 1011
            assert caught.value.reason == "Session validation unavailable"
    if phase == "before_persistence":
        persist.assert_not_awaited()
    generate.assert_not_awaited()
    assert application.state.connection_manager.active_connection_count == 0
    records = [
        r
        for r in caplog.records
        if r.getMessage() == "WebSocket session validation unavailable"
    ]
    assert records
    assert "PRIVATE" not in str([r.__dict__ for r in records])


@pytest.mark.parametrize("close_failure", [None, OSError, WebSocketDisconnect])
def test_session_failure_tolerates_an_already_closed_or_disconnected_socket(
    monkeypatch, close_failure
):
    application = create_travel_websocket_app()
    from starlette.websockets import WebSocket

    original_close = WebSocket.close
    close_calls = []

    async def close(websocket, code=1000, reason=None):
        close_calls.append(code)
        await original_close(websocket, code=code, reason=reason)
        if close_failure is not None:
            raise close_failure()

    async def authenticate(websocket):
        if close_failure is None:
            # A concurrent closer has already sent the close frame.
            await websocket.close(code=1011, reason="Session validation unavailable")
        raise TimeoutError("PRIVATE")

    persist = AsyncMock()
    monkeypatch.setattr(WebSocket, "close", close)
    monkeypatch.setattr(travel, "get_websocket_principal", authenticate)
    monkeypatch.setattr(travel, "persist_travel_request", persist)
    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(create_travel_request_event())
            with pytest.raises(WebSocketDisconnect) as caught:
                websocket.receive_json()
            assert caught.value.code == 1011
    assert close_calls == [1011]
    persist.assert_not_awaited()
    assert application.state.connection_manager.active_connection_count == 0


def test_socket_pending_limit_rejects_without_persisting_extra_work(monkeypatch):
    application = create_travel_websocket_app()
    application.state.settings.websocket_max_pending_requests = 1

    async def generate(**kwargs):
        await asyncio.sleep(60)

    persist = AsyncMock(return_value=create_accepted_request())
    monkeypatch.setattr(travel, "generate_travel_response", generate)
    monkeypatch.setattr(travel, "persist_travel_request", persist)
    with (
        TestClient(application) as client,
        client.websocket_connect("/ws/travel") as websocket,
    ):
        websocket.receive_json()
        websocket.send_json(create_travel_request_event())
        assert websocket.receive_json()["type"] == "travel.request.accepted"
        websocket.send_json(create_travel_request_event())
        rejected = websocket.receive_json()
        assert rejected["type"] == "travel.request.rejected"
        assert rejected["payload"]["code"] == "capacity_exceeded"
    persist.assert_awaited_once()


def create_travel_request_event(
    *,
    client_message_id: UUID | None = None,
    conversation_id: UUID | None = None,
    trip_id: UUID | None = None,
) -> dict[str, object]:
    """Return one valid travel request envelope."""

    payload: dict[str, object] = {
        "client_message_id": str(client_message_id or uuid4()),
        "message": "Plan a three-day trip to Lahore",
        "locale": "en-PK",
    }
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    if trip_id is not None:
        payload["trip_id"] = str(trip_id)
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


def test_travel_websocket_acknowledges_a_valid_travel_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid request should receive a typed correlation acknowledgement."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()
    persist_request = AsyncMock(
        return_value=create_accepted_request(conversation_id=conversation_id)
    )
    monkeypatch.setattr(travel, "persist_travel_request", persist_request)

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
    assert event.payload.conversation_id == conversation_id
    assert event.sent_at.tzinfo is not None
    assert set(message["payload"]) == {"client_message_id", "conversation_id"}
    persist_request.assert_awaited_once()
    call = persist_request.await_args.kwargs
    assert call["event"].payload.client_message_id == client_message_id
    assert call["user_id"] is not None
    assert call["session_factory"] is application.state.session_factory


def test_travel_websocket_reports_an_existing_response_as_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unowned active claim should become a processing protocol event."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()
    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        AsyncMock(
            return_value=create_accepted_request(conversation_id=conversation_id)
        ),
    )

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(
                create_travel_request_event(client_message_id=client_message_id)
            )
            websocket.receive_json()
            message = websocket.receive_json()

    event = TravelResponseProcessingEvent.model_validate(message)
    assert event.payload.client_message_id == client_message_id
    assert event.payload.conversation_id == conversation_id


def test_travel_websocket_reports_a_completed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generated persisted message should become a completed protocol event."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()
    assistant_message = MagicMock(spec=Message)
    assistant_message.id = uuid4()
    assistant_message.content = "Three-day Lahore itinerary"
    itinerary_id = uuid4()
    rich_content = AssistantRichContent.model_validate(
        {
            "type": "rich_response",
            "schema_version": 1,
            "sections": [
                {
                    "type": "place_carousel",
                    "id": "suggested-places",
                    "title": "Suggested for You",
                    "items": [
                        {
                            "id": "gion-district",
                            "name": "Gion District",
                            "location": "Kyoto",
                        }
                    ],
                }
            ],
        }
    )
    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        AsyncMock(
            return_value=create_accepted_request(conversation_id=conversation_id)
        ),
    )
    monkeypatch.setattr(
        travel,
        "generate_travel_response",
        AsyncMock(
            return_value=TravelResponseResult(
                message=assistant_message,
                is_cached=False,
                is_processing=False,
                error_code=None,
                itinerary_id=itinerary_id,
                rich_content=rich_content,
            )
        ),
    )

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(
                create_travel_request_event(client_message_id=client_message_id)
            )
            websocket.receive_json()
            message = websocket.receive_json()

    event = TravelResponseCompletedEvent.model_validate(message)
    assert event.payload.client_message_id == client_message_id
    assert event.payload.conversation_id == conversation_id
    assert event.payload.assistant_message_id == assistant_message.id
    assert event.payload.content == "Three-day Lahore itinerary"
    assert event.payload.itinerary_id == itinerary_id
    assert event.payload.is_duplicate is False
    assert event.payload.structured_content == rich_content


def test_travel_websocket_returns_structured_airport_input_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Airport ambiguity should become Flutter controls, not prose parsing."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()
    assistant_message = MagicMock(spec=Message)
    assistant_message.id = uuid4()
    assistant_message.content = "Select a London airport."
    clarification = TravelClarification(
        requests=[
            AirportInputRequest(
                field="origin_airport",
                query="lindon",
                status="selection_required",
                question="Select an airport for lindon.",
                options=[
                    AirportOption(
                        provider_location_id="london-city",
                        iata_code="LON",
                        location_type="city",
                        name="London",
                        city_name="London",
                        country_name="United Kingdom",
                        country_code="GB",
                    ),
                    AirportOption(
                        provider_location_id="stansted",
                        iata_code="STN",
                        location_type="airport",
                        name="London Stansted Airport",
                        city_name="London",
                        country_name="United Kingdom",
                        country_code="GB",
                    ),
                ],
            )
        ]
    )
    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        AsyncMock(
            return_value=create_accepted_request(conversation_id=conversation_id)
        ),
    )
    monkeypatch.setattr(
        travel,
        "generate_travel_response",
        AsyncMock(
            return_value=TravelResponseResult(
                message=assistant_message,
                is_cached=False,
                is_processing=False,
                error_code=None,
                clarification=clarification,
            )
        ),
    )

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(
                create_travel_request_event(client_message_id=client_message_id)
            )
            websocket.receive_json()
            message = websocket.receive_json()

    event = TravelInputRequiredEvent.model_validate(message)
    assert event.payload.client_message_id == client_message_id
    assert event.payload.conversation_id == conversation_id
    assert event.payload.assistant_message_id == assistant_message.id
    assert event.payload.content == "Select a London airport."
    request = event.payload.clarification.requests[0]
    assert request.field == "origin_airport"
    assert [option.iata_code for option in request.options] == ["LON", "STN"]


def test_travel_websocket_reports_a_safe_model_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider errors must not leak details through the WebSocket protocol."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()
    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        AsyncMock(
            return_value=create_accepted_request(conversation_id=conversation_id)
        ),
    )
    monkeypatch.setattr(
        travel,
        "generate_travel_response",
        AsyncMock(side_effect=ModelGatewayError("provider secret detail")),
    )

    with caplog.at_level(logging.WARNING, logger=travel.__name__):
        with TestClient(application) as client:
            with client.websocket_connect("/ws/travel") as websocket:
                websocket.receive_json()
                websocket.send_json(
                    create_travel_request_event(client_message_id=client_message_id)
                )
                websocket.receive_json()
                message = websocket.receive_json()

    event = TravelResponseFailedEvent.model_validate(message)
    assert event.payload.client_message_id == client_message_id
    assert event.payload.conversation_id == conversation_id
    assert event.payload.code is TravelResponseErrorCode.PROVIDER_ERROR
    assert "provider secret detail" not in str(message)
    record = next(
        record
        for record in caplog.records
        if record.message == "Travel response generation failed"
    )
    assert record.error_code == "provider_error"
    assert record.error_type == "ModelGatewayError"
    assert "provider secret detail" not in record.getMessage()


def test_travel_websocket_reports_exhausted_model_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry exhaustion should become a stable client-visible failure event."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()
    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        AsyncMock(
            return_value=create_accepted_request(conversation_id=conversation_id)
        ),
    )
    monkeypatch.setattr(
        travel,
        "generate_travel_response",
        AsyncMock(
            return_value=TravelResponseResult(
                message=None,
                is_cached=False,
                is_processing=False,
                error_code=TravelResponseErrorCode.ATTEMPTS_EXHAUSTED,
            )
        ),
    )

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(
                create_travel_request_event(client_message_id=client_message_id)
            )
            websocket.receive_json()
            message = websocket.receive_json()

    event = TravelResponseFailedEvent.model_validate(message)
    assert event.payload.code is TravelResponseErrorCode.ATTEMPTS_EXHAUSTED


def test_travel_websocket_correlates_repeated_travel_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sequential requests should each echo their own client-message ID."""

    application = create_travel_websocket_app()
    first_message_id = uuid4()
    second_message_id = uuid4()
    first_conversation_id = uuid4()
    second_conversation_id = uuid4()
    persist_request = AsyncMock(
        side_effect=[
            create_accepted_request(conversation_id=first_conversation_id),
            create_accepted_request(conversation_id=second_conversation_id),
        ]
    )
    monkeypatch.setattr(travel, "persist_travel_request", persist_request)

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()

            websocket.send_json(
                create_travel_request_event(
                    client_message_id=first_message_id,
                    conversation_id=first_conversation_id,
                )
            )
            first_acknowledgement = websocket.receive_json()
            first_processing = websocket.receive_json()

            websocket.send_json(
                create_travel_request_event(
                    client_message_id=second_message_id,
                )
            )
            second_acknowledgement = websocket.receive_json()
            second_processing = websocket.receive_json()

    assert first_acknowledgement["payload"]["client_message_id"] == str(
        first_message_id
    )
    assert second_acknowledgement["payload"]["client_message_id"] == str(
        second_message_id
    )
    assert first_acknowledgement["payload"]["conversation_id"] == str(
        first_conversation_id
    )
    assert second_acknowledgement["payload"]["conversation_id"] == str(
        second_conversation_id
    )
    assert first_processing["type"] == "travel.response.processing"
    assert second_processing["type"] == "travel.response.processing"
    assert persist_request.await_count == 2


def test_travel_websocket_keeps_heartbeat_routing_after_a_travel_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acknowledging a request should leave the connection ready for heartbeats."""

    application = create_travel_websocket_app()
    persist_request = AsyncMock(return_value=create_accepted_request())
    monkeypatch.setattr(travel, "persist_travel_request", persist_request)

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(create_travel_request_event())
            acknowledgement = websocket.receive_json()
            processing = websocket.receive_json()

            websocket.send_json(create_ping_event())
            pong = websocket.receive_json()

    assert acknowledgement["type"] == "travel.request.accepted"
    assert processing["type"] == "travel.response.processing"
    assert pong["type"] == "connection.pong"


def test_travel_websocket_pongs_while_a_model_response_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow response task must not block the WebSocket receive loop."""

    application = create_travel_websocket_app()

    async def delayed_response(**_kwargs) -> TravelResponseResult:
        await asyncio.sleep(0.2)
        return TravelResponseResult(
            message=None,
            is_cached=False,
            is_processing=True,
            error_code=None,
        )

    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        AsyncMock(return_value=create_accepted_request()),
    )
    monkeypatch.setattr(travel, "generate_travel_response", delayed_response)

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(create_travel_request_event())
            acknowledgement = websocket.receive_json()

            websocket.send_json(create_ping_event())
            pong = websocket.receive_json()
            processing = websocket.receive_json()

    assert acknowledgement["type"] == "travel.request.accepted"
    assert pong["type"] == "connection.pong"
    assert processing["type"] == "travel.response.processing"


def test_travel_websocket_acknowledges_an_idempotent_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable duplicate should receive the same successful protocol shape."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()
    persist_request = AsyncMock(
        return_value=create_accepted_request(
            conversation_id=conversation_id,
            is_duplicate=True,
        )
    )
    monkeypatch.setattr(travel, "persist_travel_request", persist_request)

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(
                create_travel_request_event(client_message_id=client_message_id)
            )
            message = websocket.receive_json()

    event = TravelRequestAcceptedEvent.model_validate(message)
    assert event.payload.client_message_id == client_message_id
    assert event.payload.conversation_id == conversation_id


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (ConversationNotFoundError("not found"), "conversation_not_found"),
        (ClientMessageConflictError("conflict"), "client_message_conflict"),
        (TripNotFoundError("not found"), "trip_not_found"),
    ],
)
def test_travel_websocket_rejects_a_domain_failure_without_closing(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_code: str,
) -> None:
    """A request-specific domain failure should preserve the socket."""

    application = create_travel_websocket_app()
    client_message_id = uuid4()
    persist_request = AsyncMock(side_effect=error)
    monkeypatch.setattr(travel, "persist_travel_request", persist_request)

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as websocket:
            websocket.receive_json()
            websocket.send_json(
                create_travel_request_event(client_message_id=client_message_id)
            )
            rejection_message = websocket.receive_json()

            websocket.send_json(create_ping_event())
            pong_message = websocket.receive_json()

    event = TravelRequestRejectedEvent.model_validate(rejection_message)
    assert event.payload.client_message_id == client_message_id
    assert event.payload.code == expected_code
    assert set(rejection_message["payload"]) == {"client_message_id", "code"}
    assert pong_message["type"] == "connection.pong"


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


def test_reconnect_resends_same_request_and_receives_completed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()

    generation_started = Event()
    generation_cancelled = Event()
    connection_cleaned_up = Event()
    manager = application.state.connection_manager
    original_unregister = manager.unregister

    async def unregister_connection(*, connection_id):
        await original_unregister(connection_id=connection_id)
        connection_cleaned_up.set()

    monkeypatch.setattr(
        manager,
        "unregister",
        unregister_connection,
    )

    assistant_message = MagicMock(spec=Message)
    assistant_message.id = uuid4()
    assistant_message.content = "Your recovered itinerary"

    persist_request = AsyncMock(
        side_effect=[
            create_accepted_request(
                conversation_id=conversation_id,
            ),
            create_accepted_request(
                conversation_id=conversation_id,
                is_duplicate=True,
            ),
        ]
    )

    attempts = 0

    async def generate_response(**_kwargs) -> TravelResponseResult:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            generation_started.set()

            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                generation_cancelled.set()
                raise

        return TravelResponseResult(
            message=assistant_message,
            is_cached=False,
            is_processing=False,
            error_code=None,
        )

    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        persist_request,
    )
    monkeypatch.setattr(
        travel,
        "generate_travel_response",
        generate_response,
    )

    request = create_travel_request_event(
        client_message_id=client_message_id,
        conversation_id=conversation_id,
    )

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as first_socket:
            first_ready = ConnectionReadyEvent.model_validate(
                first_socket.receive_json()
            )

            first_socket.send_json(request)

            accepted = TravelRequestAcceptedEvent.model_validate(
                first_socket.receive_json()
            )
            assert accepted.payload.client_message_id == client_message_id
            assert accepted.payload.conversation_id == conversation_id

            assert generation_started.wait(timeout=5)
            # Finish endpoint cleanup before TestClient exits its scope.
            first_socket.close(code=1000)

            assert generation_cancelled.wait(timeout=5)
            assert connection_cleaned_up.wait(timeout=5)

        # Closing the first connection must cancel its pending task.
        assert generation_cancelled.wait(timeout=5)

        with client.websocket_connect("/ws/travel") as second_socket:
            second_ready = ConnectionReadyEvent.model_validate(
                second_socket.receive_json()
            )

            assert (
                second_ready.payload.connection_id != first_ready.payload.connection_id
            )

            # Replay exactly the same request identifiers and content.
            second_socket.send_json(request)

            accepted_again = TravelRequestAcceptedEvent.model_validate(
                second_socket.receive_json()
            )
            completed = TravelResponseCompletedEvent.model_validate(
                second_socket.receive_json()
            )

            assert accepted_again.payload.client_message_id == client_message_id
            assert accepted_again.payload.conversation_id == conversation_id

            assert completed.payload.client_message_id == client_message_id
            assert completed.payload.conversation_id == conversation_id
            assert completed.payload.assistant_message_id == assistant_message.id
            assert completed.payload.content == assistant_message.content

    assert attempts == 2
    assert persist_request.await_count == 2


def test_reconnect_delivers_cached_reply_after_delivery_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_travel_websocket_app()
    client_message_id = uuid4()
    conversation_id = uuid4()
    itinerary_id = uuid4()

    reply_saved = Event()
    connection_cleaned_up = Event()

    assistant_message = MagicMock(spec=Message)
    assistant_message.id = uuid4()
    assistant_message.content = "Your saved itinerary"

    manager = application.state.connection_manager
    original_unregister = manager.unregister

    async def unregister_connection(*, connection_id):
        await original_unregister(connection_id=connection_id)
        connection_cleaned_up.set()

    monkeypatch.setattr(
        manager,
        "unregister",
        unregister_connection,
    )

    persist_request = AsyncMock(
        side_effect=[
            create_accepted_request(
                conversation_id=conversation_id,
            ),
            create_accepted_request(
                conversation_id=conversation_id,
                is_duplicate=True,
            ),
        ]
    )
    monkeypatch.setattr(
        travel,
        "persist_travel_request",
        persist_request,
    )

    attempts = 0

    async def generate_response(**_kwargs) -> TravelResponseResult:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            # Simulate a committed reply before delivery to the client.
            reply_saved.set()
            await asyncio.Future()

        return TravelResponseResult(
            message=assistant_message,
            is_cached=True,
            is_processing=False,
            error_code=None,
            itinerary_id=itinerary_id,
        )

    monkeypatch.setattr(
        travel,
        "generate_travel_response",
        generate_response,
    )

    request = create_travel_request_event(
        client_message_id=client_message_id,
        conversation_id=conversation_id,
    )

    with TestClient(application) as client:
        with client.websocket_connect("/ws/travel") as first_socket:
            first_socket.receive_json()
            first_socket.send_json(request)

            accepted = TravelRequestAcceptedEvent.model_validate(
                first_socket.receive_json()
            )
            assert accepted.payload.client_message_id == client_message_id
            assert reply_saved.wait(timeout=5)

            first_socket.close(code=1000)
            assert connection_cleaned_up.wait(timeout=5)

        connection_cleaned_up.clear()

        with client.websocket_connect("/ws/travel") as second_socket:
            second_socket.receive_json()
            second_socket.send_json(request)

            accepted_again = TravelRequestAcceptedEvent.model_validate(
                second_socket.receive_json()
            )
            completed = TravelResponseCompletedEvent.model_validate(
                second_socket.receive_json()
            )

            assert accepted_again.payload.client_message_id == client_message_id
            assert accepted_again.payload.conversation_id == conversation_id

            assert completed.payload.client_message_id == client_message_id
            assert completed.payload.conversation_id == conversation_id
            assert completed.payload.assistant_message_id == assistant_message.id
            assert completed.payload.content == assistant_message.content
            assert completed.payload.itinerary_id == itinerary_id
            assert completed.payload.is_duplicate is True

            second_socket.close(code=1000)
            assert connection_cleaned_up.wait(timeout=5)

    assert attempts == 2
    assert persist_request.await_count == 2
