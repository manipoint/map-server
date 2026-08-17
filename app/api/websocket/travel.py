"""Travel WebSocket endpoint."""

from asyncio import wait_for
from json import JSONDecodeError, loads
from uuid import UUID

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
    WebSocketSessionFactoryDependency,
    WebSocketSettingsDependency,
)
from app.api.websocket.events import (
    ConnectionPingEvent,
    ConnectionPongEvent,
    ConnectionReadyEvent,
    ConnectionReadyPayload,
    TravelRequestAcceptedEvent,
    TravelRequestAcceptedPayload,
    TravelRequestEvent,
    TravelRequestRejectedEvent,
    TravelRequestRejectedPayload,
    validate_client_event,
)
from app.database.session import AsyncSessionFactory
from app.domain.errors import ClientMessageConflictError, ConversationNotFoundError
from app.services.conversation_service import AcceptedTravelRequest, ConversationService

router = APIRouter(tags=["travel-websocket"])


async def persist_travel_request(
    *, event: TravelRequestEvent, user_id: UUID, session_factory: AsyncSessionFactory
) -> AcceptedTravelRequest:
    """Persist one travel request using a short-lived database session."""
    async with session_factory() as database_session:
        service = ConversationService(session=database_session)
        return await service.accept_request(
            user_id=user_id,
            client_message_id=event.payload.client_message_id,
            conversation_id=event.payload.conversation_id,
            message=event.payload.message,
            locale=event.payload.locale,
        )


@router.websocket("/ws/travel", name="travel_websocket")
async def travel_websocket(
    websocket: WebSocket,
    principal: WebSocketPrincipal,
    connection_manager: ConnectionManagerDependency,
    settings: WebSocketSettingsDependency,
    session_factory: WebSocketSessionFactoryDependency,
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
                client_event = validate_client_event(raw_event)
            except ValidationError:
                await websocket.close(
                    code=WS_POLICY_VIOLATION_CODE,
                    reason=WS_POLICY_VIOLATION_REASON,
                )
                return
            if isinstance(client_event, ConnectionPingEvent):
                pong_event = ConnectionPongEvent()
                await websocket.send_json(pong_event.model_dump(mode="json"))
                continue
            try:
                accepted_request = await persist_travel_request(
                    event=client_event,
                    user_id=principal.user.id,
                    session_factory=session_factory,
                )
            except ConversationNotFoundError:
                rejected_event = TravelRequestRejectedEvent(
                    payload=TravelRequestRejectedPayload(
                        client_message_id=client_event.payload.client_message_id,
                        code="conversation_not_found",
                    )
                )

                await websocket.send_json(rejected_event.model_dump(mode="json"))
                continue
            except ClientMessageConflictError:
                rejected_event = TravelRequestRejectedEvent(
                    payload=TravelRequestRejectedPayload(
                        client_message_id=client_event.payload.client_message_id,
                        code="client_message_conflict",
                    )
                )
                await websocket.send_json(rejected_event.model_dump(mode="json"))
                continue
            accepted_event = TravelRequestAcceptedEvent(
                payload=TravelRequestAcceptedPayload(
                    client_message_id=client_event.payload.client_message_id,
                    conversation_id=accepted_request.conversation.id,
                )
            )
            await websocket.send_json(accepted_event.model_dump(mode="json"))
    finally:
        await connection_manager.unregister(connection_id=connection.connection_id)
