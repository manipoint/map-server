"""Tests for the travel conversation message repository."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.message import Message
from app.database.repositories.messages import MessageRepository


def create_mock_session() -> Mock:
    """Create an asynchronous database-session mock."""

    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


def test_client_message_lookup_returns_the_matching_user_message() -> None:
    """An existing user-owned client message should be returned."""

    message = Mock(spec=Message)
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = message
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    result = asyncio.run(
        repository.get_user_message_by_client_id(
            user_id=uuid4(),
            client_message_id=uuid4(),
        )
    )

    assert result is message
    session.execute.assert_awaited_once()


def test_client_message_lookup_returns_none_when_not_found() -> None:
    """An unknown or differently owned client message should return None."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    result = asyncio.run(
        repository.get_user_message_by_client_id(
            user_id=uuid4(),
            client_message_id=uuid4(),
        )
    )

    assert result is None


def test_client_message_lookup_filters_by_owner_id_and_role() -> None:
    """Idempotency lookup should enforce ownership and user-message shape."""

    user_id = uuid4()
    client_message_id = uuid4()
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    asyncio.run(
        repository.get_user_message_by_client_id(
            user_id=user_id,
            client_message_id=client_message_id,
        )
    )

    statement = session.execute.await_args.args[0]
    compiled_statement = str(statement.compile())
    parameter_values = statement.compile().params.values()

    assert statement.column_descriptions[0]["entity"] is Message
    assert user_id in parameter_values
    assert client_message_id in parameter_values
    assert "JOIN app.conversations" in compiled_statement
    assert "conversations.user_id" in compiled_statement
    assert "messages.client_message_id" in compiled_statement
    assert "messages.role" in compiled_statement
    assert "user" in parameter_values


def test_client_message_lookup_does_not_lock_by_default() -> None:
    """A normal idempotency lookup should not acquire a row lock."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    asyncio.run(
        repository.get_user_message_by_client_id(
            user_id=uuid4(),
            client_message_id=uuid4(),
        )
    )

    statement = session.execute.await_args.args[0]
    assert "FOR UPDATE" not in str(statement.compile())


def test_client_message_lookup_can_lock_an_existing_message() -> None:
    """Duplicate processing should be able to lock an existing message row."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    asyncio.run(
        repository.get_user_message_by_client_id(
            user_id=uuid4(),
            client_message_id=uuid4(),
            for_update=True,
        )
    )

    statement = session.execute.await_args.args[0]
    assert "FOR UPDATE" in str(statement.compile())


def test_assistant_reply_lookup_returns_the_latest_owned_reply() -> None:
    """The latest assistant reply to a user-owned message should be returned."""

    user_id = uuid4()
    reply_to_message_id = uuid4()
    reply = Mock(spec=Message)
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = reply
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    result = asyncio.run(
        repository.get_assistant_reply(
            user_id=user_id,
            reply_to_message_id=reply_to_message_id,
        )
    )

    assert result is reply
    statement = session.execute.await_args.args[0]
    compiled_statement = str(statement.compile())
    parameter_values = statement.compile().params.values()
    assert "JOIN app.conversations" in compiled_statement
    assert "conversations.user_id" in compiled_statement
    assert "messages.reply_to_message_id" in compiled_statement
    assert "messages.role" in compiled_statement
    assert (
        "ORDER BY app.messages.created_at DESC, app.messages.id DESC"
        in compiled_statement
    )
    assert user_id in parameter_values
    assert reply_to_message_id in parameter_values
    assert "assistant" in parameter_values
    assert statement._limit_clause.value == 1


def test_assistant_reply_lookup_returns_none_when_not_found() -> None:
    """A missing or differently owned assistant reply should return None."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    result = asyncio.run(
        repository.get_assistant_reply(
            user_id=uuid4(),
            reply_to_message_id=uuid4(),
        )
    )

    assert result is None


def test_recent_messages_are_returned_in_chronological_order() -> None:
    """The repository should reverse the newest-first database result."""

    oldest = Mock(spec=Message)
    middle = Mock(spec=Message)
    newest = Mock(spec=Message)
    scalar_result = Mock()
    scalar_result.all.return_value = [newest, middle, oldest]
    query_result = Mock()
    query_result.scalars.return_value = scalar_result
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    result = asyncio.run(
        repository.list_recent_by_conversation(
            conversation_id=uuid4(),
            user_id=uuid4(),
            limit=3,
        )
    )

    assert result == [oldest, middle, newest]


def test_recent_messages_query_enforces_owner_conversation_and_limit() -> None:
    """History loading should be ownership-safe and bounded for LLM context."""

    conversation_id = uuid4()
    user_id = uuid4()
    scalar_result = Mock()
    scalar_result.all.return_value = []
    query_result = Mock()
    query_result.scalars.return_value = scalar_result
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = MessageRepository(session)

    asyncio.run(
        repository.list_recent_by_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=12,
        )
    )

    statement = session.execute.await_args.args[0]
    compiled_statement = str(statement.compile())
    parameter_values = statement.compile().params.values()
    assert "JOIN app.conversations" in compiled_statement
    assert "messages.conversation_id" in compiled_statement
    assert "conversations.user_id" in compiled_statement
    assert (
        "ORDER BY app.messages.created_at DESC, app.messages.id DESC"
        in compiled_statement
    )
    assert conversation_id in parameter_values
    assert user_id in parameter_values
    assert statement._limit_clause.value == 12


def test_recent_messages_rejects_a_non_positive_limit() -> None:
    """An unbounded or empty history request should fail before querying the DB."""

    session = create_mock_session()
    repository = MessageRepository(session)

    for invalid_limit in (0, -1):
        try:
            asyncio.run(
                repository.list_recent_by_conversation(
                    conversation_id=uuid4(),
                    user_id=uuid4(),
                    limit=invalid_limit,
                )
            )
        except ValueError as error:
            assert str(error) == "limit must be greater than zero"
        else:
            raise AssertionError("Expected a ValueError for a non-positive limit")

    session.execute.assert_not_awaited()


def test_create_user_message_adds_and_flushes_without_commit() -> None:
    """A user message should retain its client idempotency identifier."""

    conversation_id = uuid4()
    client_message_id = uuid4()
    session = create_mock_session()
    repository = MessageRepository(session)

    message = asyncio.run(
        repository.create_user_message(
            conversation_id=conversation_id,
            client_message_id=client_message_id,
            content="Plan a weekend in Lahore",
        )
    )

    assert message.conversation_id == conversation_id
    assert message.client_message_id == client_message_id
    assert message.reply_to_message_id is None
    assert message.role == "user"
    assert message.content == "Plan a weekend in Lahore"
    session.add.assert_called_once_with(message)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_create_assistant_message_adds_and_flushes_without_commit() -> None:
    """An assistant message should reference the user message it answers."""

    conversation_id = uuid4()
    reply_to_message_id = uuid4()
    session = create_mock_session()
    repository = MessageRepository(session)

    message = asyncio.run(
        repository.create_assistant_message(
            conversation_id=conversation_id,
            reply_to_message_id=reply_to_message_id,
            content="Here is your Lahore itinerary.",
        )
    )

    assert message.conversation_id == conversation_id
    assert message.client_message_id is None
    assert message.reply_to_message_id == reply_to_message_id
    assert message.role == "assistant"
    assert message.content == "Here is your Lahore itinerary."
    session.add.assert_called_once_with(message)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
