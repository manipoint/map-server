"""Unit tests for travel WebSocket persistence helpers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.websocket import travel
from app.api.websocket.events import TravelRequestEvent
from app.database.models.conversation import Conversation
from app.database.models.message import Message
from app.services.conversation_service import (
    AcceptedTravelRequest,
    ConversationService,
)


class DatabaseSessionContext:
    """Track entry and exit for one short-lived database session."""

    def __init__(self, database_session: MagicMock) -> None:
        self.database_session = database_session
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> MagicMock:
        self.entered = True
        return self.database_session

    async def __aexit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        self.exited = True


def test_persist_travel_request_uses_a_short_lived_service_session(
    monkeypatch,
) -> None:
    """Persistence should forward typed input and release its database session."""

    user_id = uuid4()
    client_message_id = uuid4()
    requested_conversation_id = uuid4()
    durable_conversation = MagicMock(spec=Conversation)
    durable_conversation.id = requested_conversation_id
    user_message = MagicMock(spec=Message)
    accepted_request = AcceptedTravelRequest(
        conversation=durable_conversation,
        user_message=user_message,
        is_duplicate=False,
    )
    event = TravelRequestEvent.model_validate(
        {
            "version": 1,
            "type": "travel.request",
            "sent_at": "2026-08-16T12:30:00Z",
            "payload": {
                "client_message_id": str(client_message_id),
                "conversation_id": str(requested_conversation_id),
                "message": "Plan a trip to Lahore",
                "locale": "ur-PK",
            },
        }
    )

    database_session = MagicMock(spec=AsyncSession)
    session_context = DatabaseSessionContext(database_session)
    session_factory = Mock(return_value=session_context)
    service = MagicMock(spec=ConversationService)
    service.accept_request = AsyncMock(return_value=accepted_request)
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(travel, "ConversationService", service_factory)

    result = asyncio.run(
        travel.persist_travel_request(
            event=event,
            user_id=user_id,
            session_factory=session_factory,
        )
    )

    assert result is accepted_request
    session_factory.assert_called_once_with()
    service_factory.assert_called_once_with(session=database_session)
    service.accept_request.assert_awaited_once_with(
        user_id=user_id,
        client_message_id=client_message_id,
        conversation_id=requested_conversation_id,
        message="Plan a trip to Lahore",
        locale="ur-PK",
    )
    assert session_context.entered is True
    assert session_context.exited is True
