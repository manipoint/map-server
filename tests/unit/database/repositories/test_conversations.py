"""Tests for the travel conversation repository."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation import Conversation
from app.database.repositories.conversations import ConversationRepository


def create_mock_session() -> Mock:
    """Create an asynchronous database-session mock."""

    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


def test_create_adds_and_flushes_conversation_without_commit() -> None:
    """Creating a conversation should flush but leave commit to the service."""

    user_id = uuid4()
    session = create_mock_session()
    repository = ConversationRepository(session)

    conversation = asyncio.run(
        repository.create(
            user_id=user_id,
            locale="ur-PK",
            title="Three days in Lahore",
        )
    )

    assert conversation.user_id == user_id
    assert conversation.locale == "ur-PK"
    assert conversation.title == "Three days in Lahore"
    session.add.assert_called_once_with(conversation)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_owner_lookup_returns_the_matching_conversation() -> None:
    """An existing user-owned conversation should be returned."""

    conversation = Mock(spec=Conversation)
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = conversation
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = ConversationRepository(session)

    result = asyncio.run(
        repository.get_by_id_for_user(
            conversation_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert result is conversation
    session.execute.assert_awaited_once()


def test_owner_lookup_returns_none_for_a_missing_conversation() -> None:
    """A missing or differently owned conversation should return None."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = ConversationRepository(session)

    result = asyncio.run(
        repository.get_by_id_for_user(
            conversation_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert result is None


def test_owner_lookup_filters_by_conversation_and_user() -> None:
    """Conversation lookup must include both identity and ownership predicates."""

    conversation_id = uuid4()
    user_id = uuid4()
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = ConversationRepository(session)

    asyncio.run(
        repository.get_by_id_for_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )
    )

    statement = session.execute.await_args.args[0]
    compiled_statement = str(statement.compile())
    parameter_values = statement.compile().params.values()

    assert statement.column_descriptions[0]["entity"] is Conversation
    assert conversation_id in parameter_values
    assert user_id in parameter_values
    assert "conversations.id" in compiled_statement
    assert "conversations.user_id" in compiled_statement


def test_owner_lookup_does_not_lock_by_default() -> None:
    """Read-only conversation lookup should not acquire a row lock."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = ConversationRepository(session)

    asyncio.run(
        repository.get_by_id_for_user(
            conversation_id=uuid4(),
            user_id=uuid4(),
        )
    )

    statement = session.execute.await_args.args[0]
    assert "FOR UPDATE" not in str(statement.compile())


def test_owner_lookup_can_lock_the_conversation() -> None:
    """Message processing should be able to lock a conversation row."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = ConversationRepository(session)

    asyncio.run(
        repository.get_by_id_for_user(
            conversation_id=uuid4(),
            user_id=uuid4(),
            for_update=True,
        )
    )

    statement = session.execute.await_args.args[0]
    assert "FOR UPDATE" in str(statement.compile())
