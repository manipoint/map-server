"""Travel WebSocket endpoint."""

import logging
from asyncio import (
    CancelledError,
    Lock,
    Task,
    create_task,
    current_task,
    gather,
    sleep,
    wait_for,
)
from json import JSONDecodeError, loads
from uuid import UUID

from anyio import CancelScope
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.websockets import WebSocketState

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
    create_travel_response_service,
    get_websocket_principal,
)
from app.api.websocket.events import (
    ConnectionPingEvent,
    ConnectionPongEvent,
    ConnectionReadyEvent,
    ConnectionReadyPayload,
    TravelInputRequiredEvent,
    TravelInputRequiredPayload,
    TravelRequestAcceptedEvent,
    TravelRequestAcceptedPayload,
    TravelRequestEvent,
    TravelRequestRejectedEvent,
    TravelRequestRejectedPayload,
    TravelResponseCompletedEvent,
    TravelResponseCompletedPayload,
    TravelResponseFailedEvent,
    TravelResponseFailedPayload,
    TravelResponseProcessingEvent,
    TravelResponseProcessingPayload,
    validate_client_event,
)
from app.common.time import utc_now
from app.database.session import AsyncSessionFactory
from app.domain.enums import TravelResponseErrorCode
from app.domain.errors import (
    ClientMessageConflictError,
    ConversationNotFoundError,
    TripNotFoundError,
)
from app.graph.subgraphs.model_gateway import ModelGatewayError
from app.services.conversation_service import AcceptedTravelRequest, ConversationService
from app.services.travel_response_service import TravelResponseResult

router = APIRouter(tags=["travel-websocket"])
logger = logging.getLogger(__name__)


async def persist_travel_request(
    *,
    event: TravelRequestEvent,
    user_id: UUID,
    session_factory: AsyncSessionFactory,
) -> AcceptedTravelRequest:
    """Persist one travel request using a short-lived database session."""
    async with session_factory() as database_session:
        conversation_service = ConversationService(session=database_session)
        accepted_request = await conversation_service.accept_request(
            user_id=user_id,
            client_message_id=event.payload.client_message_id,
            conversation_id=event.payload.conversation_id,
            trip_id=event.payload.trip_id,
            message=event.payload.message,
            locale=event.payload.locale,
        )

        return accepted_request


async def generate_travel_response(
    *,
    websocket: WebSocket,
    user_id: UUID,
    accepted_request: AcceptedTravelRequest,
    session_factory: AsyncSessionFactory,
) -> TravelResponseResult:
    """Generate one response using a separate short-lived database session."""

    async with session_factory() as database_session:
        response_service = create_travel_response_service(
            websocket=websocket,
            database_session=database_session,
        )
        return await response_service.generate_reply(
            user_id=user_id,
            accepted_request=accepted_request,
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
    send_lock = Lock()
    response_tasks: set[Task[None]] = set()

    async def close_invalid_session(*, code: int, reason: str) -> None:
        """Cancel generation and serialize closing with other socket writes."""
        for task in tuple(response_tasks):
            if task is not current_task():
                task.cancel()
        try:
            async with send_lock:
                if (
                    websocket.application_state == WebSocketState.CONNECTED
                    and websocket.client_state == WebSocketState.CONNECTED
                ):
                    await websocket.close(code=code, reason=reason)
        except (WebSocketDisconnect, OSError):
            # The peer can disappear while session validation is running.
            pass

    async def check_session() -> bool:
        close_code: int
        close_reason: str
        try:
            current = await get_websocket_principal(websocket)
            if current.auth_session.id != principal.auth_session.id:
                raise WebSocketException(code=1008, reason="Session changed")
            return True
        except CancelledError:
            raise
        except WebSocketException as error:
            close_code = error.code
            close_reason = error.reason or "Authentication required"
        except (TimeoutError, OSError, SQLAlchemyError) as error:
            logger.warning(
                "WebSocket session validation unavailable",
                extra={
                    "connection_id": str(connection.connection_id),
                    "error_type": type(error).__name__,
                },
            )
            close_code = 1011
            close_reason = "Session validation unavailable"
        await close_invalid_session(code=close_code, reason=close_reason)
        return False

    async def monitor_session() -> None:
        try:
            while True:
                remaining = (principal.claims.expires_at - utc_now()).total_seconds()
                await sleep(
                    max(0, min(settings.websocket_auth_check_seconds, remaining))
                )
                if not await check_session():
                    return
        except CancelledError:
            raise
        except Exception:
            # Failure to validate a session must not leave paid work running.
            await close_invalid_session(
                code=1011, reason="Session validation unavailable"
            )

    auth_task = create_task(monitor_session())

    async def send_event(event_data: dict[str, object]) -> None:
        """Serialize outbound events for this WebSocket connection."""

        async with send_lock:
            await websocket.send_json(event_data)

    async def send_failed_response(
        *,
        event: TravelRequestEvent,
        accepted_request: AcceptedTravelRequest,
        code: TravelResponseErrorCode,
        error_type: str | None = None,
    ) -> None:
        """Send a safe failure response without leaking provider details."""

        log_context = {
            "connection_id": str(connection.connection_id),
            "client_message_id": str(event.payload.client_message_id),
            "conversation_id": str(accepted_request.conversation.id),
            "error_code": code.value,
        }
        if error_type is not None:
            log_context["error_type"] = error_type
        logger.warning(
            "Travel response generation failed",
            extra=log_context,
        )
        failed_event = TravelResponseFailedEvent(
            payload=TravelResponseFailedPayload(
                client_message_id=event.payload.client_message_id,
                conversation_id=accepted_request.conversation.id,
                code=code,
            )
        )
        await send_event(failed_event.model_dump(mode="json"))

    async def process_response(
        *,
        event: TravelRequestEvent,
        accepted_request: AcceptedTravelRequest,
    ) -> None:
        """Generate and deliver one travel response without blocking receives."""

        try:
            try:
                if not await check_session():
                    return
                response_result = await generate_travel_response(
                    websocket=websocket,
                    user_id=principal.user.id,
                    accepted_request=accepted_request,
                    session_factory=session_factory,
                )
            except ModelGatewayError as error:
                await send_failed_response(
                    event=event,
                    accepted_request=accepted_request,
                    code=TravelResponseErrorCode.PROVIDER_ERROR,
                    error_type=type(error).__name__,
                )
                return
            except Exception as error:
                await send_failed_response(
                    event=event,
                    accepted_request=accepted_request,
                    code=TravelResponseErrorCode.GENERATION_FAILED,
                    error_type=type(error).__name__,
                )
                return

            if response_result.error_code is not None:
                await send_failed_response(
                    event=event,
                    accepted_request=accepted_request,
                    code=response_result.error_code,
                )
                return

            if response_result.is_processing:
                processing_event = TravelResponseProcessingEvent(
                    payload=TravelResponseProcessingPayload(
                        client_message_id=event.payload.client_message_id,
                        conversation_id=accepted_request.conversation.id,
                    )
                )
                await send_event(processing_event.model_dump(mode="json"))
                return

            if response_result.message is None:
                await send_failed_response(
                    event=event,
                    accepted_request=accepted_request,
                    code=TravelResponseErrorCode.GENERATION_FAILED,
                )
                return

            is_duplicate = accepted_request.is_duplicate or response_result.is_cached
            if response_result.clarification is not None:
                input_required_event = TravelInputRequiredEvent(
                    payload=TravelInputRequiredPayload(
                        client_message_id=event.payload.client_message_id,
                        conversation_id=accepted_request.conversation.id,
                        assistant_message_id=response_result.message.id,
                        content=response_result.message.content,
                        is_duplicate=is_duplicate,
                        clarification=response_result.clarification,
                    )
                )
                await send_event(input_required_event.model_dump(mode="json"))
                return

            completed_event = TravelResponseCompletedEvent(
                payload=TravelResponseCompletedPayload(
                    client_message_id=event.payload.client_message_id,
                    conversation_id=accepted_request.conversation.id,
                    assistant_message_id=response_result.message.id,
                    content=response_result.message.content,
                    is_duplicate=is_duplicate,
                    itinerary_id=response_result.itinerary_id,
                    structured_content=response_result.rich_content,
                )
            )
            await send_event(completed_event.model_dump(mode="json"))
        except CancelledError:
            raise
        except Exception:
            logger.warning(
                "Failed to deliver travel response event",
                extra={
                    "connection_id": str(connection.connection_id),
                    "client_message_id": str(event.payload.client_message_id),
                },
                exc_info=True,
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
        await send_event(
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
                await send_event(pong_event.model_dump(mode="json"))
                continue
            if not await check_session():
                return
            if len(response_tasks) >= settings.websocket_max_pending_requests:
                await send_event(
                    TravelRequestRejectedEvent(
                        payload=TravelRequestRejectedPayload(
                            client_message_id=client_event.payload.client_message_id,
                            code="capacity_exceeded",
                        )
                    ).model_dump(mode="json")
                )
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

                await send_event(rejected_event.model_dump(mode="json"))
                continue
            except ClientMessageConflictError:
                rejected_event = TravelRequestRejectedEvent(
                    payload=TravelRequestRejectedPayload(
                        client_message_id=client_event.payload.client_message_id,
                        code="client_message_conflict",
                    )
                )
                await send_event(rejected_event.model_dump(mode="json"))
                continue
            except TripNotFoundError:
                rejected_event = TravelRequestRejectedEvent(
                    payload=TravelRequestRejectedPayload(
                        client_message_id=client_event.payload.client_message_id,
                        code="trip_not_found",
                    )
                )
                await send_event(rejected_event.model_dump(mode="json"))
                continue
            accepted_event = TravelRequestAcceptedEvent(
                payload=TravelRequestAcceptedPayload(
                    client_message_id=client_event.payload.client_message_id,
                    conversation_id=accepted_request.conversation.id,
                )
            )
            await send_event(accepted_event.model_dump(mode="json"))
            response_task = create_task(
                process_response(
                    event=client_event,
                    accepted_request=accepted_request,
                )
            )
            response_tasks.add(response_task)
            response_task.add_done_callback(response_tasks.discard)
    finally:
        with CancelScope(shield=True):
            auth_task.cancel()
            pending_tasks = (auth_task, *tuple(response_tasks))
            for response_task in pending_tasks:
                response_task.cancel()
            await gather(*pending_tasks, return_exceptions=True)
            await connection_manager.unregister(connection_id=connection.connection_id)
