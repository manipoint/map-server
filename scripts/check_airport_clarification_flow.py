"""Verify structured airport clarification through the public WebSocket."""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from websockets.asyncio.client import connect

from app.api.websocket.events import (
    TravelInputRequiredEvent,
    TravelRequestEvent,
    TravelRequestPayload,
    TravelResponseCompletedEvent,
)
from app.common.time import utc_now
from app.config import get_settings
from app.observability.logging import configure_logging
from scripts.check_itinerary_flow import (
    DEFAULT_BASE_URL,
    build_websocket_url,
    parse_server_event,
    receive_terminal_event,
    require_access_token,
)

logger = logging.getLogger(__name__)

DEFAULT_MESSAGE = (
    "lindon se lhaore ana hy 10 September 2026 ko, "
    "1 adult ke liye one-way flight search karo"
)
DEFAULT_SELECTION_MESSAGE = (
    "Origin ke liye LON use karo. Destination LHE hi rakho "
    "aur flight search continue karo."
)
AirportField = Literal["origin_airport", "destination_airport"]


@dataclass(frozen=True, slots=True)
class AirportClarificationSmokeResult:
    """Verified clarification and selection across two correlated turns."""

    client_message_id: UUID
    conversation_id: UUID
    assistant_message_id: UUID
    field: AirportField
    option_codes: tuple[str, ...]
    selection_client_message_id: UUID
    completion_assistant_message_id: UUID
    completion_content: str


def build_airport_selection_message(
    *,
    field: AirportField,
    code: str,
) -> str:
    """Build a minimal follow-up that mirrors one Flutter option selection."""

    normalized_code = code.strip().upper()
    if not normalized_code:
        raise ValueError("selected airport code must not be empty")
    label = "Origin" if field == "origin_airport" else "Destination"
    return (
        f"{label} ke liye {normalized_code} use karo aur flight search continue karo."
    )


def validate_airport_clarification(
    *,
    event_data: object,
    expected_field: AirportField,
    expected_code: str,
) -> TravelInputRequiredEvent:
    """Validate the terminal event and one expected airport option."""

    event = TravelInputRequiredEvent.model_validate(event_data)
    matching_request = next(
        (
            request
            for request in event.payload.clarification.requests
            if request.field == expected_field
        ),
        None,
    )
    if matching_request is None:
        raise RuntimeError(
            f"clarification did not request the expected field: {expected_field}"
        )
    normalized_code = expected_code.strip().upper()
    option_codes = {option.iata_code for option in matching_request.options}
    if normalized_code not in option_codes:
        raise RuntimeError(
            f"clarification did not contain expected airport code: {normalized_code}"
        )
    return event


def validate_airport_selection_completion(
    *,
    event_data: object,
    expected_conversation_id: UUID | None = None,
) -> TravelResponseCompletedEvent:
    """Require the selected airport to finish without another input loop."""

    if isinstance(event_data, dict) and event_data.get("type") == (
        "travel.input.required"
    ):
        event = TravelInputRequiredEvent.model_validate(event_data)
        fields = ", ".join(
            request.field for request in event.payload.clarification.requests
        )
        raise RuntimeError(
            f"airport selection caused another clarification loop for: {fields}"
        )
    if not isinstance(event_data, dict) or event_data.get("type") != (
        "travel.response.completed"
    ):
        event_type = (
            str(event_data.get("type"))
            if isinstance(event_data, dict)
            else type(event_data).__name__
        )
        raise RuntimeError(f"flight search did not complete: {event_type}")
    event = TravelResponseCompletedEvent.model_validate(event_data)
    if (
        expected_conversation_id is not None
        and event.payload.conversation_id != expected_conversation_id
    ):
        raise RuntimeError("flight completion used an unexpected conversation")
    return event


async def check_airport_clarification_flow(
    *,
    access_token: str,
    base_url: str = DEFAULT_BASE_URL,
    message: str = DEFAULT_MESSAGE,
    locale: str = "ur-PK",
    conversation_id: UUID | None = None,
    expected_field: AirportField = "origin_airport",
    expected_code: str = "LON",
    selection_message: str | None = None,
    timeout_seconds: float = 90.0,
) -> AirportClarificationSmokeResult:
    """Verify clarification and selection across one public socket."""

    if timeout_seconds <= 0:
        raise ValueError("timeout must be greater than zero")
    normalized_message = message.strip()
    if not 1 <= len(normalized_message) <= 2000:
        raise ValueError("message must contain between 1 and 2000 characters")
    resolved_selection_message = selection_message or build_airport_selection_message(
        field=expected_field,
        code=expected_code,
    )
    normalized_selection_message = resolved_selection_message.strip()
    if not 1 <= len(normalized_selection_message) <= 2000:
        raise ValueError("selection message must contain between 1 and 2000 characters")

    settings = get_settings()
    configure_logging(settings.log_level)
    client_message_id = uuid4()
    request = TravelRequestEvent(
        version=1,
        sent_at=utc_now(),
        type="travel.request",
        payload=TravelRequestPayload(
            client_message_id=client_message_id,
            conversation_id=conversation_id,
            message=normalized_message,
            locale=locale,
        ),
    )

    async with connect(
        build_websocket_url(base_url),
        additional_headers={"Authorization": f"Bearer {access_token}"},
        open_timeout=min(timeout_seconds, 10.0),
        close_timeout=5.0,
        max_size=settings.websocket_max_message_bytes,
    ) as websocket:
        ready_event = parse_server_event(
            await asyncio.wait_for(websocket.recv(), timeout=min(timeout_seconds, 10.0))
        )
        if ready_event["type"] != "connection.ready":
            raise RuntimeError("server did not send connection.ready")

        await websocket.send(request.model_dump_json())
        terminal_event = await receive_terminal_event(
            websocket=websocket,
            client_message_id=client_message_id,
            heartbeat_interval_seconds=float(
                ready_event["payload"]["heartbeat_interval_seconds"]
            ),
            total_timeout_seconds=timeout_seconds,
        )
        if terminal_event["type"] != "travel.input.required":
            content = str(terminal_event.get("payload", {}).get("content", ""))
            detail = f" Assistant response: {content[:500]}" if content else ""
            raise RuntimeError(
                f"response completed without structured airport clarification.{detail}"
            )
        event = validate_airport_clarification(
            event_data=terminal_event,
            expected_field=expected_field,
            expected_code=expected_code,
        )
        selection_client_message_id = uuid4()
        selection_request = TravelRequestEvent(
            version=1,
            sent_at=utc_now(),
            type="travel.request",
            payload=TravelRequestPayload(
                client_message_id=selection_client_message_id,
                conversation_id=event.payload.conversation_id,
                message=normalized_selection_message,
                locale=locale,
            ),
        )
        await websocket.send(selection_request.model_dump_json())
        selection_terminal_event = await receive_terminal_event(
            websocket=websocket,
            client_message_id=selection_client_message_id,
            heartbeat_interval_seconds=float(
                ready_event["payload"]["heartbeat_interval_seconds"]
            ),
            total_timeout_seconds=timeout_seconds,
        )
        completion_event = validate_airport_selection_completion(
            event_data=selection_terminal_event,
            expected_conversation_id=event.payload.conversation_id,
        )

    matching_request = next(
        request
        for request in event.payload.clarification.requests
        if request.field == expected_field
    )
    result = AirportClarificationSmokeResult(
        client_message_id=client_message_id,
        conversation_id=event.payload.conversation_id,
        assistant_message_id=event.payload.assistant_message_id,
        field=expected_field,
        option_codes=tuple(option.iata_code for option in matching_request.options),
        selection_client_message_id=selection_client_message_id,
        completion_assistant_message_id=(completion_event.payload.assistant_message_id),
        completion_content=completion_event.payload.content,
    )
    logger.info(
        "Airport clarification and selection WebSocket smoke check completed",
        extra={
            "conversation_id": str(result.conversation_id),
            "clarification_assistant_message_id": str(result.assistant_message_id),
            "completion_assistant_message_id": str(
                result.completion_assistant_message_id
            ),
            "field": result.field,
            "option_codes": list(result.option_codes),
            "selected_code": expected_code.strip().upper(),
        },
    )
    return result


def parse_arguments() -> argparse.Namespace:
    """Parse bounded public WebSocket clarification-check options."""

    parser = argparse.ArgumentParser(
        description=(
            "Verify airport clarification and selected-code continuation over WebSocket."
        )
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--conversation-id", type=UUID)
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--locale", default="ur-PK")
    parser.add_argument(
        "--expected-field",
        choices=("origin_airport", "destination_airport"),
        default="origin_airport",
    )
    parser.add_argument("--expected-code", default="LON")
    parser.add_argument("--selection-message")
    parser.add_argument("--timeout", type=float, default=90.0)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(
        check_airport_clarification_flow(
            access_token=require_access_token(),
            base_url=arguments.base_url,
            message=arguments.message,
            locale=arguments.locale,
            conversation_id=arguments.conversation_id,
            expected_field=arguments.expected_field,
            expected_code=arguments.expected_code,
            selection_message=arguments.selection_message,
            timeout_seconds=arguments.timeout,
        )
    )
