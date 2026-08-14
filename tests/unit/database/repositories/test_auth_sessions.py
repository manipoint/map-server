"""Tests for the authentication-session repository."""

import asyncio
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.auth_session import AuthSession
from app.database.repositories.auth_sessions import AuthSessionRepository


def create_mock_session() -> Mock:
    """Create an async database-session mock."""

    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.get = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


def test_get_by_id_delegates_to_database_session() -> None:
    """Session ID lookup should use the SQLAlchemy identity lookup."""

    auth_session_id = uuid4()
    auth_session = Mock(spec=AuthSession)

    session = create_mock_session()
    session.get.return_value = auth_session
    repository = AuthSessionRepository(session)

    result = asyncio.run(repository.get_by_id(auth_session_id))

    assert result is auth_session
    session.get.assert_awaited_once_with(
        AuthSession,
        auth_session_id,
    )


def test_get_by_refresh_token_hash_returns_session() -> None:
    """A matching refresh-token hash should return its session."""

    token_hash = "stored-refresh-token-hash"
    auth_session = Mock(spec=AuthSession)
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = auth_session

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    result = asyncio.run(repository.get_by_refresh_token_hash(token_hash))
    assert result is auth_session
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert token_hash in statement.compile().params.values()


def test_get_by_refresh_token_hash_returns_none() -> None:
    """An unknown refresh-token hash should return None."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    result = asyncio.run(repository.get_by_refresh_token_hash("unknown-token-hash"))

    assert result is None


def test_refresh_token_lookup_can_lock_database_row() -> None:
    """Refresh rotation should support a database row lock."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    asyncio.run(
        repository.get_by_refresh_token_hash(
            "stored-refresh-token-hash",
            for_update=True,
        )
    )

    statement = session.execute.await_args.args[0]
    compiled_statement = str(statement.compile())

    assert "FOR UPDATE" in compiled_statement


def test_create_adds_and_flushes_session_without_commit() -> None:
    """Creating a session should flush without committing."""

    user_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=30)
    client_ip = ip_address("203.0.113.10")

    session = create_mock_session()
    repository = AuthSessionRepository(session)

    auth_session = asyncio.run(
        repository.create(
            user_id=user_id,
            refresh_token_hash="stored-refresh-token-hash",
            device_id="flutter-device-123456",
            device_name="Imran's iPhone",
            expires_at=expires_at,
            ip_address=client_ip,
            user_agent="TravelAssistant/1.0",
        )
    )

    assert auth_session.user_id == user_id
    assert auth_session.refresh_token_hash == "stored-refresh-token-hash"
    assert auth_session.device_id == "flutter-device-123456"
    assert auth_session.device_name == "Imran's iPhone"
    assert auth_session.expires_at == expires_at
    assert auth_session.ip_address == client_ip
    assert auth_session.user_agent == "TravelAssistant/1.0"
    assert auth_session.token_family_id is not None

    session.add.assert_called_once_with(auth_session)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_create_preserves_existing_token_family() -> None:
    """A rotated session should retain its token-family ID."""

    token_family_id = uuid4()
    session = create_mock_session()
    repository = AuthSessionRepository(session)

    auth_session = asyncio.run(
        repository.create(
            user_id=uuid4(),
            refresh_token_hash="new-refresh-token-hash",
            device_id="flutter-device-123456",
            device_name=None,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            token_family_id=token_family_id,
        )
    )

    assert auth_session.token_family_id == token_family_id


def test_list_active_by_user_returns_database_results() -> None:
    """Only active sessions returned by the query should be listed."""

    user_id = uuid4()
    current_time = datetime.now(UTC)
    first_session = Mock(spec=AuthSession)
    second_session = Mock(spec=AuthSession)

    query_result = Mock()
    query_result.scalars.return_value.all.return_value = [
        first_session,
        second_session,
    ]

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    result = asyncio.run(
        repository.list_active_by_user(
            user_id,
            current_time=current_time,
        )
    )

    assert result == [first_session, second_session]

    statement = session.execute.await_args.args[0]
    compiled_statement = str(statement.compile())

    assert user_id in statement.compile().params.values()
    assert current_time in statement.compile().params.values()
    assert "revoked_at IS NULL" in compiled_statement
    assert "rotated_at IS NULL" in compiled_statement
    assert "expires_at >" in compiled_statement
    assert "ORDER BY" in compiled_statement


def test_revoke_marks_active_session_as_revoked() -> None:
    """Revoking an active session should store its time and reason."""

    revoked_at = datetime.now(UTC)
    auth_session = Mock(spec=AuthSession)
    auth_session.revoked_at = None
    auth_session.revoke_reason = None

    session = create_mock_session()
    repository = AuthSessionRepository(session)

    changed = asyncio.run(
        repository.revoke(
            auth_session,
            revoked_at=revoked_at,
            reason="user_logout",
        )
    )

    assert changed is True
    assert auth_session.revoked_at == revoked_at
    assert auth_session.revoke_reason == "user_logout"
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_revoke_is_idempotent_for_revoked_session() -> None:
    """Repeated logout should not alter an existing revocation."""

    original_revoked_at = datetime.now(UTC) - timedelta(minutes=5)
    auth_session = Mock(spec=AuthSession)
    auth_session.revoked_at = original_revoked_at
    auth_session.revoke_reason = "security_event"

    session = create_mock_session()
    repository = AuthSessionRepository(session)

    changed = asyncio.run(
        repository.revoke(
            auth_session,
            revoked_at=datetime.now(UTC),
            reason="user_logout",
        )
    )

    assert changed is False
    assert auth_session.revoked_at == original_revoked_at
    assert auth_session.revoke_reason == "security_event"
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_revoke_all_marks_every_active_session() -> None:
    """Logout-all should revoke every active user session."""

    user_id = uuid4()
    revoked_at = datetime.now(UTC)

    first_session = Mock(spec=AuthSession)
    first_session.revoked_at = None
    first_session.revoke_reason = None

    second_session = Mock(spec=AuthSession)
    second_session.revoked_at = None
    second_session.revoke_reason = None

    query_result = Mock()
    query_result.scalars.return_value.all.return_value = [
        first_session,
        second_session,
    ]

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    revoked_count = asyncio.run(
        repository.revoke_all_by_user(
            user_id,
            revoked_at=revoked_at,
            reason="user_logout_all",
        )
    )

    assert revoked_count == 2

    assert first_session.revoked_at == revoked_at
    assert first_session.revoke_reason == "user_logout_all"
    assert second_session.revoked_at == revoked_at
    assert second_session.revoke_reason == "user_logout_all"

    statement = session.execute.await_args.args[0]
    assert "FOR UPDATE" in str(statement.compile())

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_revoke_all_does_not_flush_when_no_active_sessions() -> None:
    """Logout-all should succeed when the user has no active sessions."""

    query_result = Mock()
    query_result.scalars.return_value.all.return_value = []

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    revoked_count = asyncio.run(
        repository.revoke_all_by_user(
            uuid4(),
            revoked_at=datetime.now(UTC),
            reason="user_logout_all",
        )
    )

    assert revoked_count == 0
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_mark_rotated_links_replacement_session() -> None:
    """Rotation should consume the old session and link its replacement."""

    rotated_at = datetime.now(UTC)
    replacement_session_id = uuid4()

    auth_session = Mock(spec=AuthSession)
    auth_session.rotated_at = None
    auth_session.revoked_at = None
    auth_session.replaced_by_session_id = None
    auth_session.last_used_at = None

    session = create_mock_session()
    repository = AuthSessionRepository(session)

    changed = asyncio.run(
        repository.mark_rotated(
            auth_session,
            replacement_session_id=replacement_session_id,
            rotated_at=rotated_at,
        )
    )

    assert changed is True
    assert auth_session.rotated_at == rotated_at
    assert auth_session.last_used_at == rotated_at
    assert auth_session.replaced_by_session_id == replacement_session_id
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_mark_rotated_does_not_change_rotated_session() -> None:
    """A consumed refresh session should not be rotated again."""

    original_rotated_at = datetime.now(UTC) - timedelta(minutes=1)
    original_replacement_id = uuid4()

    auth_session = Mock(spec=AuthSession)
    auth_session.rotated_at = original_rotated_at
    auth_session.revoked_at = None
    auth_session.replaced_by_session_id = original_replacement_id
    auth_session.last_used_at = original_rotated_at

    session = create_mock_session()
    repository = AuthSessionRepository(session)

    changed = asyncio.run(
        repository.mark_rotated(
            auth_session,
            replacement_session_id=uuid4(),
            rotated_at=datetime.now(UTC),
        )
    )

    assert changed is False
    assert auth_session.rotated_at == original_rotated_at
    assert auth_session.replaced_by_session_id == original_replacement_id
    assert auth_session.last_used_at == original_rotated_at
    session.flush.assert_not_awaited()


def test_mark_rotated_rejects_revoked_session() -> None:
    """A revoked session should never produce a replacement session."""

    revoked_at = datetime.now(UTC) - timedelta(minutes=1)
    auth_session = Mock(spec=AuthSession)
    auth_session.rotated_at = None
    auth_session.revoked_at = revoked_at
    auth_session.replaced_by_session_id = None

    session = create_mock_session()
    repository = AuthSessionRepository(session)

    changed = asyncio.run(
        repository.mark_rotated(
            auth_session,
            replacement_session_id=uuid4(),
            rotated_at=datetime.now(UTC),
        )
    )

    assert changed is False
    assert auth_session.rotated_at is None
    assert auth_session.replaced_by_session_id is None
    session.flush.assert_not_awaited()


def test_revoke_token_family_revokes_every_session() -> None:
    """Token reuse should revoke the complete refresh-token family."""

    token_family_id = uuid4()
    revoked_at = datetime.now(UTC)

    old_session = Mock(spec=AuthSession)
    old_session.revoked_at = None
    old_session.revoke_reason = None

    current_session = Mock(spec=AuthSession)
    current_session.revoked_at = None
    current_session.revoke_reason = None

    query_result = Mock()
    query_result.scalars.return_value.all.return_value = [
        old_session,
        current_session,
    ]

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    revoked_count = asyncio.run(
        repository.revoke_token_family(
            token_family_id,
            revoked_at=revoked_at,
            reason="refresh_token_reuse",
        )
    )

    assert revoked_count == 2
    assert old_session.revoked_at == revoked_at
    assert current_session.revoked_at == revoked_at
    assert old_session.revoke_reason == "refresh_token_reuse"
    assert current_session.revoke_reason == "refresh_token_reuse"

    statement = session.execute.await_args.args[0]
    assert token_family_id in statement.compile().params.values()
    assert "FOR UPDATE" in str(statement.compile())

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_revoke_token_family_handles_empty_family() -> None:
    """Revoking an empty token family should be a successful no-op."""

    query_result = Mock()
    query_result.scalars.return_value.all.return_value = []

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    revoked_count = asyncio.run(
        repository.revoke_token_family(
            uuid4(),
            revoked_at=datetime.now(UTC),
            reason="refresh_token_reuse",
        )
    )

    assert revoked_count == 0
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_get_by_id_for_user_returns_owned_session() -> None:
    """A matching user and session ID should return the session."""

    user_id = uuid4()
    auth_session_id = uuid4()
    auth_session = Mock(spec=AuthSession)

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = auth_session

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    result = asyncio.run(
        repository.get_by_id_for_user(
            session_id=auth_session_id,
            user_id=user_id,
        )
    )

    assert result is auth_session
    session.execute.assert_awaited_once()

    statement = session.execute.await_args.args[0]
    parameters = statement.compile().params.values()

    assert auth_session_id in parameters
    assert user_id in parameters
    assert "FOR UPDATE" not in str(statement.compile())


def test_get_by_id_for_user_returns_none_for_unowned_session() -> None:
    """A session outside the user's scope should return None."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    result = asyncio.run(
        repository.get_by_id_for_user(
            session_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert result is None
    session.execute.assert_awaited_once()


def test_get_by_id_for_user_can_lock_owned_session() -> None:
    """Logout should be able to lock the owned session row."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None

    session = create_mock_session()
    session.execute.return_value = query_result
    repository = AuthSessionRepository(session)

    asyncio.run(
        repository.get_by_id_for_user(
            session_id=uuid4(),
            user_id=uuid4(),
            for_update=True,
        )
    )

    statement = session.execute.await_args.args[0]

    assert "FOR UPDATE" in str(statement.compile())
