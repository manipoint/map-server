"""Travel WebSocket endpoint."""

from asyncio import wait_for
from json import JSONDecodeError, loads

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError

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
from app.api.websocket.dependencies import (
    ConnectionManagerDependency,
    WebSocketPrincipal,
    WebSocketSettingsDependency,
)
from app.api.websocket.events import (
    ConnectionPingEvent,
    ConnectionPongEvent,
    ConnectionReadyEvent,
    ConnectionReadyPayload,
)

router = APIRouter(tags=["travel-websocket"])


@router.websocket("/ws/travel", name="travel_websocket")
async def travel_websocket(
    websocket: WebSocket,
    principal: WebSocketPrincipal,
    connection_manager: ConnectionManagerDependency,
    settings: WebSocketSettingsDependency,
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
            payload=ConnectionReadyPayload(
                connection_id=connection.connection_id,
                heartbeat_interval_seconds=settings.websocket_heartbeat_interval_seconds,
                idle_timeout_seconds=settings.websocket_idle_timeout_seconds,
                max_message_bytes=settings.websocket_max_message_bytes,
            )
        )
        await websocket.send_json(
            ready_event.model_dump(mode="json"),
        )
        while True:
            try:
                message = await wait_for(
                    websocket.receive(),
                    timeout=settings.websocket_idle_timeout_seconds,
                )
            except TimeoutError:
                await websocket.close(
                    code=WS_IDLE_TIMEOUT_CODE,
                    reason=WS_IDLE_TIMEOUT_REASON,
                )
                return

            if message["type"] == "websocket.disconnect":
                return

            text = message.get("text")
            if text is None:
                await websocket.close(
                    code=WS_UNSUPPORTED_DATA_CODE,
                    reason=WS_UNSUPPORTED_DATA_REASON,
                )
                return
            message_size = len(
                text.encode("utf-8"),
            )
            if message_size > settings.websocket_max_message_bytes:
                await websocket.close(
                    code=WS_MESSAGE_TOO_LARGE_CODE,
                    reason=WS_MESSAGE_TOO_LARGE_REASON,
                )
                return
            try:
                raw_event = loads(text)
            except (JSONDecodeError, TypeError):
                await websocket.close(
                    code=WS_INVALID_PAYLOAD_CODE,
                    reason=WS_INVALID_PAYLOAD_REASON,
                )
                return

            try:
                ConnectionPingEvent.model_validate(raw_event)
            except ValidationError:
                await websocket.close(
                    code=WS_POLICY_VIOLATION_CODE,
                    reason=WS_POLICY_VIOLATION_REASON,
                )
                return

            pong_event = ConnectionPongEvent()
            await websocket.send_json(pong_event.model_dump(mode="json"))
    finally:
        await connection_manager.unregister(connection_id=connection.connection_id)
