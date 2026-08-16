"""Versioned WebSocket event schemas."""

from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

PROTOCOL_VERSION: Final = 1


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
