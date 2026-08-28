"""Tests for conversation persistence use cases."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation import Conversation
from app.database.models.message import Message
from app.database.models.trip import Trip
from app.database.repositories.conversations import ConversationRepository
from app.database.repositories.messages import MessageRepository
from app.database.repositories.trips import TripRepository
from app.domain.errors import (
    ClientMessageConflictError,
    ConversationNotFoundError,
    TripNotFoundError,
)
from app.services.conversation_service import (
    ConversationService,
    derive_conversation_title,
)


def create_service() -> tuple[ConversationService, Mock, Mock, Mock]:
    """Create a service with mocked transaction and repository dependencies."""

    session = Mock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    conversations = Mock(spec=ConversationRepository)
    conversations.create = AsyncMock()
    conversations.get_by_id_for_user = AsyncMock()

    messages = Mock(spec=MessageRepository)
    messages.get_user_message_by_client_id = AsyncMock()
    messages.create_user_message = AsyncMock()

    service = ConversationService(
        session=session,
        conversation_repository=conversations,
        message_repository=messages,
    )
    return service, session, conversations, messages


def create_conversation(*, user_id=None) -> Conversation:
    """Create an in-memory persisted-conversation representation."""

    return Conversation(
        id=uuid4(),
        user_id=user_id or uuid4(),
        title="Lahore trip",
        locale="en",
    )


def create_user_message(
    *,
    conversation_id,
    client_message_id,
    content: str = "Plan a trip to Lahore",
    trip_id=None,
) -> Message:
    """Create an in-memory persisted user-message representation."""

    return Message(
        id=uuid4(),
        conversation_id=conversation_id,
        client_message_id=client_message_id,
        trip_id=trip_id,
        reply_to_message_id=None,
        role="user",
        content=content,
    )


def create_trip(*, trip_id=None, user_id=None) -> Trip:
    """Create one active trip context for conversation tests."""

    return Trip(
        id=trip_id or uuid4(),
        user_id=user_id or uuid4(),
        origin="Karachi",
        destination="Lahore",
        start_date=datetime(2026, 9, 10, tzinfo=UTC).date(),
        end_date=datetime(2026, 9, 12, tzinfo=UTC).date(),
        status="draft",
    )


def test_derive_conversation_title_normalizes_and_limits_text() -> None:
    """A local title should be compact and avoid an extra LLM call."""

    title = derive_conversation_title("  Plan   a\ntrip  " + "x" * 200)

    assert title.startswith("Plan a trip ")
    assert len(title) == 160


def test_accept_request_creates_and_commits_a_new_conversation() -> None:
    """A request without a conversation ID should persist both records."""

    service, session, conversations, messages = create_service()
    user_id = uuid4()
    client_message_id = uuid4()
    conversation = create_conversation(user_id=user_id)
    user_message = create_user_message(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
    )
    messages.get_user_message_by_client_id.return_value = None
    conversations.create.return_value = conversation
    messages.create_user_message.return_value = user_message

    result = asyncio.run(
        service.accept_request(
            user_id=user_id,
            client_message_id=client_message_id,
            conversation_id=None,
            message="Plan a trip to Lahore",
            locale="en-PK",
        )
    )

    conversations.create.assert_awaited_once_with(
        user_id=user_id,
        locale="en-PK",
        title="Plan a trip to Lahore",
    )
    messages.create_user_message.assert_awaited_once_with(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
        trip_id=None,
        content="Plan a trip to Lahore",
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    assert result.conversation is conversation
    assert result.user_message is user_message
    assert result.is_duplicate is False
    assert result.trip_id is None


def test_accept_request_persists_an_owned_trip_context() -> None:
    """A trip-scoped request should verify ownership and persist its trip ID."""

    service, session, conversations, messages = create_service()
    trips = Mock(spec=TripRepository)
    user_id = uuid4()
    trip_id = uuid4()
    trip = create_trip(trip_id=trip_id, user_id=user_id)
    trips.get_by_id_for_user = AsyncMock(return_value=trip)
    service.trips = trips
    client_message_id = uuid4()
    conversation = create_conversation(user_id=user_id)
    user_message = create_user_message(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
        trip_id=trip_id,
    )
    messages.get_user_message_by_client_id.return_value = None
    conversations.create.return_value = conversation
    messages.create_user_message.return_value = user_message

    result = asyncio.run(
        service.accept_request(
            user_id=user_id,
            client_message_id=client_message_id,
            conversation_id=None,
            trip_id=trip_id,
            message=user_message.content,
            locale="en-PK",
        )
    )

    trips.get_by_id_for_user.assert_awaited_once_with(
        trip_id=trip_id,
        user_id=user_id,
        for_update=True,
    )
    messages.create_user_message.assert_awaited_once_with(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
        trip_id=trip_id,
        content=user_message.content,
    )
    assert result.trip_id == trip_id
    assert result.trip is trip
    session.commit.assert_awaited_once_with()


def test_accept_request_rejects_a_missing_or_foreign_trip() -> None:
    """Unknown and differently owned trips should share a safe failure."""

    service, session, conversations, messages = create_service()
    trips = Mock(spec=TripRepository)
    trips.get_by_id_for_user = AsyncMock(return_value=None)
    service.trips = trips
    messages.get_user_message_by_client_id.return_value = None

    with pytest.raises(TripNotFoundError):
        asyncio.run(
            service.accept_request(
                user_id=uuid4(),
                client_message_id=uuid4(),
                conversation_id=None,
                trip_id=uuid4(),
                message="Plan this trip",
                locale="en",
            )
        )

    conversations.create.assert_not_awaited()
    messages.create_user_message.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_accept_request_locks_and_updates_an_existing_conversation() -> None:
    """Appending should lock ownership and update conversation activity."""

    service, session, conversations, messages = create_service()
    user_id = uuid4()
    client_message_id = uuid4()
    conversation = create_conversation(user_id=user_id)
    conversation.updated_at = datetime.now(UTC) - timedelta(days=1)
    previous_updated_at = conversation.updated_at
    user_message = create_user_message(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
    )
    messages.get_user_message_by_client_id.return_value = None
    conversations.get_by_id_for_user.return_value = conversation
    messages.create_user_message.return_value = user_message

    result = asyncio.run(
        service.accept_request(
            user_id=user_id,
            client_message_id=client_message_id,
            conversation_id=conversation.id,
            message=user_message.content,
            locale="ur-PK",
        )
    )

    conversations.get_by_id_for_user.assert_awaited_once_with(
        conversation_id=conversation.id,
        user_id=user_id,
        for_update=True,
    )
    assert conversation.locale == "ur-PK"
    assert conversation.updated_at > previous_updated_at
    assert result.is_duplicate is False
    session.commit.assert_awaited_once_with()


def test_accept_request_rejects_a_missing_or_foreign_conversation() -> None:
    """Missing and differently owned conversations should share one safe error."""

    service, session, conversations, messages = create_service()
    messages.get_user_message_by_client_id.return_value = None
    conversations.get_by_id_for_user.return_value = None

    with pytest.raises(ConversationNotFoundError):
        asyncio.run(
            service.accept_request(
                user_id=uuid4(),
                client_message_id=uuid4(),
                conversation_id=uuid4(),
                message="Continue my trip",
                locale="en",
            )
        )

    messages.create_user_message.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_accept_request_returns_a_matching_duplicate_without_writing() -> None:
    """A retried client request should reuse its durable message and conversation."""

    service, session, conversations, messages = create_service()
    user_id = uuid4()
    client_message_id = uuid4()
    conversation = create_conversation(user_id=user_id)
    existing_message = create_user_message(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
    )
    messages.get_user_message_by_client_id.return_value = existing_message
    conversations.get_by_id_for_user.return_value = conversation

    result = asyncio.run(
        service.accept_request(
            user_id=user_id,
            client_message_id=client_message_id,
            conversation_id=None,
            message=existing_message.content,
            locale="en",
        )
    )

    conversations.get_by_id_for_user.assert_awaited_once_with(
        conversation_id=existing_message.conversation_id,
        user_id=user_id,
    )
    conversations.create.assert_not_awaited()
    messages.create_user_message.assert_not_awaited()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    assert result.conversation is conversation
    assert result.user_message is existing_message
    assert result.trip is None
    assert result.is_duplicate is True


def test_accept_request_reloads_trip_context_for_a_matching_duplicate() -> None:
    """An idempotent retry should restore the same trusted active trip details."""

    service, session, conversations, messages = create_service()
    user_id = uuid4()
    trip = create_trip(user_id=user_id)
    conversation = create_conversation(user_id=user_id)
    existing_message = create_user_message(
        conversation_id=conversation.id,
        client_message_id=uuid4(),
        trip_id=trip.id,
    )
    messages.get_user_message_by_client_id.return_value = existing_message
    conversations.get_by_id_for_user.return_value = conversation
    trips = Mock(spec=TripRepository)
    trips.get_by_id_for_user = AsyncMock(return_value=trip)
    service.trips = trips

    result = asyncio.run(
        service.accept_request(
            user_id=user_id,
            client_message_id=existing_message.client_message_id,
            conversation_id=conversation.id,
            trip_id=trip.id,
            message=existing_message.content,
            locale="en",
        )
    )

    assert result.trip is trip
    assert result.trip_id == trip.id
    trips.get_by_id_for_user.assert_awaited_once_with(
        trip_id=trip.id,
        user_id=user_id,
    )
    session.commit.assert_awaited_once_with()


@pytest.mark.parametrize("conflict", ["content", "conversation", "trip"])
def test_accept_request_rejects_conflicting_client_message_reuse(
    conflict: str,
) -> None:
    """An idempotency ID must not be reused for a different logical request."""

    service, session, conversations, messages = create_service()
    user_id = uuid4()
    client_message_id = uuid4()
    conversation = create_conversation(user_id=user_id)
    existing_message = create_user_message(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
    )
    messages.get_user_message_by_client_id.return_value = existing_message

    requested_conversation_id = conversation.id
    requested_content = existing_message.content
    requested_trip_id = existing_message.trip_id
    if conflict == "content":
        requested_content = "A different request"
    elif conflict == "conversation":
        requested_conversation_id = uuid4()
    else:
        requested_trip_id = uuid4()

    with pytest.raises(ClientMessageConflictError):
        asyncio.run(
            service.accept_request(
                user_id=user_id,
                client_message_id=client_message_id,
                conversation_id=requested_conversation_id,
                trip_id=requested_trip_id,
                message=requested_content,
                locale="en",
            )
        )

    conversations.create.assert_not_awaited()
    messages.create_user_message.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_accept_request_recovers_from_a_concurrent_duplicate_insert() -> None:
    """A unique-key race should reload the winner instead of duplicating work."""

    service, session, conversations, messages = create_service()
    user_id = uuid4()
    client_message_id = uuid4()
    conversation = create_conversation(user_id=user_id)
    existing_message = create_user_message(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
    )
    messages.get_user_message_by_client_id.side_effect = [None, existing_message]
    conversations.create.return_value = create_conversation(user_id=user_id)
    messages.create_user_message.side_effect = IntegrityError(
        "INSERT INTO app.messages",
        {},
        RuntimeError("duplicate key"),
    )
    conversations.get_by_id_for_user.return_value = conversation

    result = asyncio.run(
        service.accept_request(
            user_id=user_id,
            client_message_id=client_message_id,
            conversation_id=None,
            message=existing_message.content,
            locale="en",
        )
    )

    assert result.user_message is existing_message
    assert result.is_duplicate is True
    session.rollback.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()


def test_accept_request_reraises_an_unrelated_integrity_error() -> None:
    """A database failure without an owned duplicate must not be hidden."""

    service, session, conversations, messages = create_service()
    integrity_error = IntegrityError(
        "INSERT INTO app.messages",
        {},
        RuntimeError("constraint failure"),
    )
    messages.get_user_message_by_client_id.side_effect = [None, None]
    conversations.create.return_value = create_conversation()
    messages.create_user_message.side_effect = integrity_error

    with pytest.raises(IntegrityError) as raised:
        asyncio.run(
            service.accept_request(
                user_id=uuid4(),
                client_message_id=uuid4(),
                conversation_id=None,
                message="Plan a trip",
                locale="en",
            )
        )

    assert raised.value is integrity_error
    assert session.rollback.await_count == 2
    session.commit.assert_not_awaited()
