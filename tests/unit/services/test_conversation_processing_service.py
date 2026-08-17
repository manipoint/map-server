"""Tests for preparing persisted conversation context."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation import Conversation
from app.database.models.message import Message
from app.database.repositories.messages import MessageRepository
from app.services.conversation_processing_service import (
    ConversationProcessingContext,
    ConversationProcessingService,
)
from app.services.conversation_service import AcceptedTravelRequest


def create_accepted_request(*, is_duplicate: bool = False) -> AcceptedTravelRequest:
    """Create an in-memory accepted travel request."""

    conversation = Conversation(
        id=uuid4(),
        user_id=uuid4(),
        title="Lahore trip",
        locale="en",
    )
    user_message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        client_message_id=uuid4(),
        reply_to_message_id=None,
        role="user",
        content="Plan a trip to Lahore",
    )
    return AcceptedTravelRequest(
        conversation=conversation,
        user_message=user_message,
        is_duplicate=is_duplicate,
    )


def create_service(
    *, history_limit: int = 20
) -> tuple[ConversationProcessingService, Mock, Mock]:
    """Create a processing service with a mocked message repository."""

    session = Mock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    messages = Mock(spec=MessageRepository)
    messages.get_assistant_reply = AsyncMock()
    messages.list_recent_by_conversation = AsyncMock()
    messages.create_assistant_message = AsyncMock()
    service = ConversationProcessingService(
        session=session,
        history_limit=history_limit,
        message_repository=messages,
    )
    return service, session, messages


def test_processing_context_requires_generation_without_cached_reply() -> None:
    """A context without a persisted assistant response needs generation."""

    context = ConversationProcessingContext(
        accepted_request=create_accepted_request(),
        history=(),
        cached_reply=None,
    )

    assert context.should_generate_reply is True


def test_processing_context_does_not_generate_with_cached_reply() -> None:
    """A persisted assistant response should be reused."""

    accepted_request = create_accepted_request(is_duplicate=True)
    reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Here is your itinerary.",
    )
    context = ConversationProcessingContext(
        accepted_request=accepted_request,
        history=(),
        cached_reply=reply,
    )

    assert context.should_generate_reply is False


def test_prepare_returns_cached_reply_without_loading_history() -> None:
    """A completed request should avoid history loading and model work."""

    service, _, messages = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request(is_duplicate=True)
    reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Here is your itinerary.",
    )
    messages.get_assistant_reply.return_value = reply

    result = asyncio.run(
        service.prepare(user_id=user_id, accepted_request=accepted_request)
    )

    assert result.accepted_request is accepted_request
    assert result.history == ()
    assert result.cached_reply is reply
    assert result.should_generate_reply is False
    messages.get_assistant_reply.assert_awaited_once_with(
        user_id=user_id,
        reply_to_message_id=accepted_request.user_message.id,
    )
    messages.list_recent_by_conversation.assert_not_awaited()


def test_prepare_loads_bounded_history_when_reply_is_missing() -> None:
    """A new request should load limited chronological model context."""

    service, _, messages = create_service(history_limit=12)
    user_id = uuid4()
    accepted_request = create_accepted_request()
    history = [accepted_request.user_message]
    messages.get_assistant_reply.return_value = None
    messages.list_recent_by_conversation.return_value = history

    result = asyncio.run(
        service.prepare(user_id=user_id, accepted_request=accepted_request)
    )

    assert result.accepted_request is accepted_request
    assert result.history == tuple(history)
    assert result.cached_reply is None
    assert result.should_generate_reply is True
    messages.list_recent_by_conversation.assert_awaited_once_with(
        conversation_id=accepted_request.conversation.id,
        user_id=user_id,
        limit=12,
    )


@pytest.mark.parametrize("history_limit", [0, -1])
def test_service_rejects_a_non_positive_history_limit(history_limit: int) -> None:
    """History configuration must always produce a bounded non-empty query."""

    session = Mock(spec=AsyncSession)

    with pytest.raises(ValueError, match="history_limit must be greater than zero"):
        ConversationProcessingService(
            session=session,
            history_limit=history_limit,
        )


def test_save_reply_reuses_an_existing_canonical_reply() -> None:
    """An already completed request should not create another reply."""

    service, session, messages = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request(is_duplicate=True)
    existing_reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Existing itinerary",
    )
    messages.get_assistant_reply.return_value = existing_reply

    result = asyncio.run(
        service.save_reply(
            user_id=user_id,
            accepted_request=accepted_request,
            content="Newly generated itinerary",
        )
    )

    assert result.message is existing_reply
    assert result.is_duplicate is True
    messages.get_assistant_reply.assert_awaited_once_with(
        user_id=user_id,
        reply_to_message_id=accepted_request.user_message.id,
    )
    messages.create_assistant_message.assert_not_awaited()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_save_reply_creates_normalized_content_and_commits() -> None:
    """A new assistant reply should be trimmed, persisted, and committed."""

    service, session, messages = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request()
    assistant_message = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Generated itinerary",
    )
    messages.get_assistant_reply.return_value = None
    messages.create_assistant_message.return_value = assistant_message

    result = asyncio.run(
        service.save_reply(
            user_id=user_id,
            accepted_request=accepted_request,
            content="  Generated itinerary\n",
        )
    )

    assert result.message is assistant_message
    assert result.is_duplicate is False
    messages.create_assistant_message.assert_awaited_once_with(
        conversation_id=accepted_request.conversation.id,
        reply_to_message_id=accepted_request.user_message.id,
        content="Generated itinerary",
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_save_reply_recovers_the_winner_after_a_unique_race() -> None:
    """A concurrent unique conflict should return the reply committed first."""

    service, session, messages = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request()
    winning_reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Winning itinerary",
    )
    integrity_error = IntegrityError("INSERT", {}, Exception("duplicate reply"))
    messages.get_assistant_reply.side_effect = [None, winning_reply]
    messages.create_assistant_message.side_effect = integrity_error

    result = asyncio.run(
        service.save_reply(
            user_id=user_id,
            accepted_request=accepted_request,
            content="Losing itinerary",
        )
    )

    assert result.message is winning_reply
    assert result.is_duplicate is True
    assert messages.get_assistant_reply.await_count == 2
    session.rollback.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()


def test_save_reply_reraises_an_unrelated_integrity_error() -> None:
    """An integrity failure without a competing reply must not be hidden."""

    service, session, messages = create_service()
    accepted_request = create_accepted_request()
    integrity_error = IntegrityError("INSERT", {}, Exception("other constraint"))
    messages.get_assistant_reply.side_effect = [None, None]
    messages.create_assistant_message.side_effect = integrity_error

    with pytest.raises(IntegrityError) as raised_error:
        asyncio.run(
            service.save_reply(
                user_id=uuid4(),
                accepted_request=accepted_request,
                content="Generated itinerary",
            )
        )

    assert raised_error.value is integrity_error
    assert session.rollback.await_count == 2
    session.commit.assert_not_awaited()


def test_save_reply_rolls_back_an_unexpected_error() -> None:
    """Unexpected persistence failures should leave the transaction reusable."""

    service, session, messages = create_service()
    accepted_request = create_accepted_request()
    messages.get_assistant_reply.return_value = None
    messages.create_assistant_message.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.save_reply(
                user_id=uuid4(),
                accepted_request=accepted_request,
                content="Generated itinerary",
            )
        )

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("content", ["", "   ", "\n\t"])
def test_save_reply_rejects_blank_content_without_database_work(content: str) -> None:
    """A blank model response should fail before opening a transaction."""

    service, session, messages = create_service()

    with pytest.raises(
        ValueError,
        match="assistant reply content must not be blank",
    ):
        asyncio.run(
            service.save_reply(
                user_id=uuid4(),
                accepted_request=create_accepted_request(),
                content=content,
            )
        )

    messages.get_assistant_reply.assert_not_awaited()
    messages.create_assistant_message.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
