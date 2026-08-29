"""Run one authenticated itinerary request through WebSocket and REST."""

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from json import loads
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from pydantic import TypeAdapter
from websockets.asyncio.client import connect

from app.api.schemas.itineraries import ItineraryResponse
from app.api.websocket.events import (
    ConnectionPingEvent,
    EmptyPayload,
    TravelRequestEvent,
    TravelRequestPayload,
)
from app.common.time import utc_now
from app.config import get_settings
from app.observability.logging import configure_logging

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MESSAGE = (
    "Create and submit a complete day-by-day sightseeing itinerary for the active "
    "trip. This smoke check does not request flight or hotel searches. Use these "
    "fixed preferences: one adult, a balanced pace, and interests in museums, "
    "history, and parks. Call search_places at most once, cover every trip day, "
    "then call submit_itinerary exactly once."
)
TERMINAL_EVENT_TYPES = {
    "travel.input.required",
    "travel.request.rejected",
    "travel.response.completed",
    "travel.response.failed",
}
EVENT_ADAPTER = TypeAdapter(dict[str, Any])


@dataclass(frozen=True, slots=True)
class ItinerarySmokeResult:
    """Verified identifiers returned by one live itinerary flow."""

    client_message_id: UUID
    conversation_id: UUID
    assistant_message_id: UUID
    itinerary: ItineraryResponse


def build_websocket_url(base_url: str) -> str:
    """Convert an HTTP application URL into its travel WebSocket URL."""

    parsed = urlsplit(base_url.rstrip("/"))
    websocket_scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme)
    if websocket_scheme is None or not parsed.netloc:
        raise ValueError("base URL must be an absolute HTTP or HTTPS URL")

    base_path = parsed.path.rstrip("/")
    return urlunsplit(
        (websocket_scheme, parsed.netloc, f"{base_path}/ws/travel", "", "")
    )


def require_access_token(explicit_token: str | None = None) -> str:
    """Return a token without ever logging it or storing it in settings."""

    token = explicit_token or os.getenv("TRAVEL_ACCESS_TOKEN")
    if token is None or not token.strip():
        raise RuntimeError(
            "TRAVEL_ACCESS_TOKEN is required; export a valid access token first"
        )
    return token.strip()


def parse_server_event(raw_message: str | bytes) -> dict[str, Any]:
    """Decode one JSON object received from the application socket."""

    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8")
    event = EVENT_ADAPTER.validate_python(loads(raw_message))
    if not isinstance(event.get("type"), str) or not isinstance(
        event.get("payload"), dict
    ):
        raise RuntimeError("server returned an invalid WebSocket event")
    return event


async def receive_terminal_event(
    *,
    websocket: Any,
    client_message_id: UUID,
    heartbeat_interval_seconds: float,
    total_timeout_seconds: float,
) -> dict[str, Any]:
    """Wait for the correlated terminal event while keeping the socket active."""

    deadline = monotonic() + total_timeout_seconds
    heartbeat_wait = max(1.0, heartbeat_interval_seconds)

    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for the itinerary response")

        try:
            raw_message = await asyncio.wait_for(
                websocket.recv(),
                timeout=min(heartbeat_wait, remaining),
            )
        except TimeoutError:
            ping = ConnectionPingEvent(
                version=1,
                sent_at=utc_now(),
                type="connection.ping",
                payload=EmptyPayload(),
            )
            await websocket.send(ping.model_dump_json())
            continue

        event = parse_server_event(raw_message)
        event_type = event["type"]
        if event_type not in TERMINAL_EVENT_TYPES:
            continue

        payload = event["payload"]
        if payload.get("client_message_id") != str(client_message_id):
            continue
        return event


async def fetch_itinerary(
    *,
    base_url: str,
    access_token: str,
    itinerary_id: UUID,
    timeout_seconds: float,
) -> ItineraryResponse:
    """Fetch and validate the itinerary persisted by the graph handoff."""

    settings = get_settings()
    url = f"{base_url.rstrip('/')}{settings.api_v1_prefix}/itineraries/{itinerary_id}"
    async with httpx.AsyncClient(timeout=timeout_seconds) as http_client:
        response = await http_client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
    return ItineraryResponse.model_validate(response.json())


async def check_itinerary_flow(
    *,
    trip_id: UUID,
    access_token: str,
    base_url: str = DEFAULT_BASE_URL,
    message: str = DEFAULT_MESSAGE,
    locale: str = "en-PK",
    conversation_id: UUID | None = None,
    timeout_seconds: float = 90.0,
) -> ItinerarySmokeResult:
    """Spend one graph request and verify its persisted itinerary over REST."""

    if timeout_seconds <= 0:
        raise ValueError("timeout must be greater than zero")

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
            trip_id=trip_id,
            message=message,
            locale=locale,
        ),
    )

    websocket_url = build_websocket_url(base_url)
    async with connect(
        websocket_url,
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

        heartbeat_interval = float(ready_event["payload"]["heartbeat_interval_seconds"])
        await websocket.send(request.model_dump_json())
        terminal_event = await receive_terminal_event(
            websocket=websocket,
            client_message_id=client_message_id,
            heartbeat_interval_seconds=heartbeat_interval,
            total_timeout_seconds=timeout_seconds,
        )

    payload = terminal_event["payload"]
    if terminal_event["type"] != "travel.response.completed":
        code = payload.get("code", "unknown_error")
        raise RuntimeError(f"itinerary generation did not complete: {code}")

    itinerary_value = payload.get("itinerary_id")
    if itinerary_value is None:
        assistant_content = str(payload.get("content", "")).strip()
        detail = (
            f" Assistant response: {assistant_content[:500]}"
            if assistant_content
            else ""
        )
        raise RuntimeError(
            "response completed without an itinerary_id. The model returned a "
            f"normal reply instead of submit_itinerary.{detail}"
        )

    resolved_itinerary_id = UUID(itinerary_value)
    itinerary = await fetch_itinerary(
        base_url=base_url,
        access_token=access_token,
        itinerary_id=resolved_itinerary_id,
        timeout_seconds=min(timeout_seconds, 15.0),
    )
    if itinerary.trip_id != trip_id:
        raise RuntimeError("persisted itinerary belongs to a different trip")

    result = ItinerarySmokeResult(
        client_message_id=client_message_id,
        conversation_id=UUID(payload["conversation_id"]),
        assistant_message_id=UUID(payload["assistant_message_id"]),
        itinerary=itinerary,
    )
    logger.info(
        "Itinerary WebSocket smoke check completed",
        extra={
            "trip_id": str(trip_id),
            "conversation_id": str(result.conversation_id),
            "assistant_message_id": str(result.assistant_message_id),
            "itinerary_id": str(itinerary.id),
            "itinerary_version": itinerary.version,
            "itinerary_status": itinerary.status.value,
            "item_count": len(itinerary.items),
        },
    )
    return result


def parse_arguments() -> argparse.Namespace:
    """Parse the existing trip and cost-bounded smoke-check options."""

    parser = argparse.ArgumentParser(
        description=(
            "Send one live itinerary request over WebSocket and verify it over REST."
        )
    )
    parser.add_argument("trip_id", type=UUID, help="Existing trip owned by the user")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Running FastAPI base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--conversation-id",
        type=UUID,
        help="Optional existing conversation owned by the user",
    )
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--locale", default="en-PK")
    parser.add_argument("--timeout", type=float, default=90.0)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(
        check_itinerary_flow(
            trip_id=arguments.trip_id,
            access_token=require_access_token(),
            base_url=arguments.base_url,
            message=arguments.message,
            locale=arguments.locale,
            conversation_id=arguments.conversation_id,
            timeout_seconds=arguments.timeout,
        )
    )
