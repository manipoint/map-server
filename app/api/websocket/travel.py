"""Travel WebSocket endpoint."""

from json import JSONDecodeError, loads

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError

from app.api.websocket.dependencies import (
    ConnectionManagerDependency,
    WebSocketPrincipal,
)
from app.api.websocket.events import (
    ConnectionPingEvent,
    ConnectionPongEvent,
    ConnectionReadyEvent,
    ConnectionReadyPayload,
)

router = APIRouter(tags=["travel-websocket"])

WS_UNSUPPORTED_DATA_CODE = 1003
WS_INVALID_PAYLOAD_CODE = 1007
WS_POLICY_VIOLATION_CODE = 1008


@router.websocket("/ws/travel", name="travel_websocket")
async def travel_websocket(
    websocket: WebSocket,
    principal: WebSocketPrincipal,
    connection_manager: ConnectionManagerDependency,
) -> None:
    """Accept and track an authenticated travel WebSocket connection."""

    await websocket.accept()
    connection = await connection_manager.register(
        websocket=websocket,
        user_id=principal.user.id,
        session_id=principal.auth_session.id,
    )
    try:
        ready_event = ConnectionReadyEvent(
            payload=ConnectionReadyPayload(connection_id=connection.connection_id)
        )
        await websocket.send_json(
            ready_event.model_dump(mode="json"),
        )
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                return

            text = message.get("text")
            if text is None:
                await websocket.close(
                    code=WS_UNSUPPORTED_DATA_CODE,
                    reason="Text JSON messages are required",
                )
                return

            try:
                raw_event = loads(text)
            except (JSONDecodeError, TypeError):
                await websocket.close(
                    code=WS_INVALID_PAYLOAD_CODE,
                    reason="Invalid JSON payload",
                )
                return

            try:
                ConnectionPingEvent.model_validate(raw_event)
            except ValidationError:
                await websocket.close(
                    code=WS_POLICY_VIOLATION_CODE,
                    reason="Invalid client event",
                )
                return

            pong_event = ConnectionPongEvent()
            await websocket.send_json(pong_event.model_dump(mode="json"))
    finally:
        await connection_manager.unregister(connection_id=connection.connection_id)
