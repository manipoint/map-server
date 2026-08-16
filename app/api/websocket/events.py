"""Versioned WebSocket event schemas."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from app.api.websocket.constants import PROTOCOL_VERSION


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(UTC)


class WebSocketEvent(BaseModel):
    """Shared fields included in every WebSocket server event."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = PROTOCOL_VERSION
    sent_at: AwareDatetime = Field(default_factory=utc_now)


class ConnectionReadyPayload(BaseModel):
    """Information returned after successful socket authentication."""

    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    heartbeat_interval_seconds: float = Field(gt=0)
    idle_timeout_seconds: float = Field(gt=0)
    max_message_bytes: int = Field(gt=0)


class ConnectionReadyEvent(WebSocketEvent):
    """Confirm that an authenticated WebSocket connection is ready."""

    type: Literal["connection.ready"] = "connection.ready"
    payload: ConnectionReadyPayload


class EmptyPayload(BaseModel):
    """Payload for connection events that require no additional data."""

    model_config = ConfigDict(extra="forbid")


class ClientWebSocketEvent(BaseModel):
    """Fields required in every client-originated WebSocket event."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    sent_at: AwareDatetime


class ConnectionPingEvent(ClientWebSocketEvent):
    """Client heartbeat event."""

    type: Literal["connection.ping"]
    payload: EmptyPayload


class ConnectionPongEvent(WebSocketEvent):
    """Server response to a valid connection heartbeat."""

    type: Literal["connection.pong"] = "connection.pong"
    payload: EmptyPayload = Field(default_factory=EmptyPayload)


class TravelRequestPayload(BaseModel):
    """A user's natural-language travel request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_message_id: UUID
    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=2000)
    locale: str = Field(default="en", min_length=2, max_length=35)


class TravelRequestEvent(ClientWebSocketEvent):
    """A client request for travel-assistant processing."""

    type: Literal["travel.request"]
    payload: TravelRequestPayload


class TravelRequestAcceptedPayload(BaseModel):
    """Identify the client request accepted by the server."""

    model_config = ConfigDict(extra="forbid")
    client_message_id: UUID


class TravelRequestAcceptedEvent(WebSocketEvent):
    """Confirm that a valid travel request was received."""

    type: Literal["travel.request.accepted"] = "travel.request.accepted"
    payload: TravelRequestAcceptedPayload


ClientEvent = Annotated[
    ConnectionPingEvent | TravelRequestEvent,
    Field(discriminator="type"),
]
CLIENT_EVENT_ADAPTER = TypeAdapter(ClientEvent)


def validate_client_event(event_data: object) -> ClientEvent:
    """Validate and route a client event according to its type."""

    return CLIENT_EVENT_ADAPTER.validate_python(event_data)
