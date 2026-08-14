"""Tests for the user repository."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.user import User
from app.database.repositories.users import UserRepository


def create_mock_session() -> Mock:
    """Create an async database-session mock."""
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.get = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


def test_get_by_email_returns_matching_user() -> None:
    """An existing email should return its user."""

    user = User(
        email="user@example.com",
        password_hash="stored-password-hash",
    )
    result = Mock()
    result.scalar_one_or_none.return_value = user

    session = create_mock_session()
    session.execute.return_value = result
    repository = UserRepository(session)
    returned_user = asyncio.run(repository.get_by_email("user@example.com"))
    assert returned_user is user
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert statement.column_descriptions[0]["entity"] is User
    assert "user@example.com" in statement.compile().params.values()


def test_get_by_email_returns_none_when_user_does_not_exist() -> None:
    """An unknown email should return None."""

    result = Mock()
    result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = result
    repository = UserRepository(session)

    returned_user = asyncio.run(repository.get_by_email("missing@example.com"))

    assert returned_user is None
    session.execute.assert_awaited_once()


def test_get_by_id_delegates_to_session() -> None:
    """User ID lookup should use the session identity lookup."""

    user_id = uuid4()
    user = User(
        id=user_id,
        email="user@example.com",
        password_hash="stored-password-hash",
    )
    session = create_mock_session()
    session.get.return_value = user
    repository = UserRepository(session)

    returned_user = asyncio.run(repository.get_by_id(user_id))

    assert returned_user is user
    session.get.assert_awaited_once_with(User, user_id)


def test_create_adds_and_flushes_user_without_committing() -> None:
    """Creating a user should flush but leave commit to the service."""

    session = create_mock_session()
    repository = UserRepository(session)
    user = asyncio.run(
        repository.create(
            email="new@example.com",
            password_hash="stored-password-hash",
        )
    )

    assert user.email == "new@example.com"
    assert user.password_hash == "stored-password-hash"

    session.add.assert_called_once_with(user)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_update_password_hash_flushes_without_committing() -> None:
    """An upgraded password hash should be flushed without commit."""

    user = User(
        email="user@example.com",
        password_hash="old-password-hash",
    )
    session = create_mock_session()
    repository = UserRepository(session)

    asyncio.run(
        repository.update_password_hash(
            user,
            "new-password-hash",
        )
    )

    assert user.password_hash == "new-password-hash"
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
