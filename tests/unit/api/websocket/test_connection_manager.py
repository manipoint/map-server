"""Unit tests for in-process WebSocket connection tracking."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import WebSocket

from app.api.websocket.connection_manager import (
    WS_SERVICE_RESTART_CODE,
    WS_SERVICE_RESTART_REASON,
    WS_SESSION_REVOKED_CODE,
    WS_SESSION_REVOKED_REASON,
    ConnectionManager,
)


def create_websocket() -> MagicMock:
    """Create a WebSocket test double for registry operations."""

    return MagicMock(spec=WebSocket)


def test_register_returns_authenticated_connection_metadata() -> None:
    """Registration should retain socket ownership and an aware timestamp."""

    async def scenario() -> None:
        manager = ConnectionManager()
        websocket = create_websocket()
        user_id = uuid4()
        session_id = uuid4()
        started_at = datetime.now(UTC)

        connection = await manager.register(
            websocket=websocket,
            user_id=user_id,
            session_id=session_id,
        )

        assert connection.websocket is websocket
        assert connection.user_id == user_id
        assert connection.session_id == session_id
        assert connection.connected_at >= started_at
        assert connection.connected_at.tzinfo is UTC
        assert manager.active_connection_count == 1

    asyncio.run(scenario())


def test_register_creates_unique_ids_for_separate_connections() -> None:
    """Multiple sockets for one session must remain independently addressable."""

    async def scenario() -> None:
        manager = ConnectionManager()
        user_id = uuid4()
        session_id = uuid4()

        first = await manager.register(
            websocket=create_websocket(),
            user_id=user_id,
            session_id=session_id,
        )
        second = await manager.register(
            websocket=create_websocket(),
            user_id=user_id,
            session_id=session_id,
        )

        assert first.connection_id != second.connection_id
        assert manager.active_connection_count == 2
        assert manager._user_connections[user_id] == {
            first.connection_id,
            second.connection_id,
        }
        assert manager._session_connections[session_id] == {
            first.connection_id,
            second.connection_id,
        }

    asyncio.run(scenario())


def test_unregister_removes_one_connection_but_preserves_shared_indexes() -> None:
    """Removing one socket must not remove another socket for the same session."""

    async def scenario() -> None:
        manager = ConnectionManager()
        user_id = uuid4()
        session_id = uuid4()
        first = await manager.register(
            websocket=create_websocket(),
            user_id=user_id,
            session_id=session_id,
        )
        second = await manager.register(
            websocket=create_websocket(),
            user_id=user_id,
            session_id=session_id,
        )

        removed = await manager.unregister(first.connection_id)

        assert removed is first
        assert manager.active_connection_count == 1
        assert first.connection_id not in manager._connections
        assert manager._user_connections[user_id] == {second.connection_id}
        assert manager._session_connections[session_id] == {second.connection_id}

    asyncio.run(scenario())


def test_unregister_cleans_empty_user_and_session_indexes() -> None:
    """The final socket removal should not leave empty secondary-index sets."""

    async def scenario() -> None:
        manager = ConnectionManager()
        user_id = uuid4()
        session_id = uuid4()
        connection = await manager.register(
            websocket=create_websocket(),
            user_id=user_id,
            session_id=session_id,
        )

        removed = await manager.unregister(connection.connection_id)

        assert removed is connection
        assert manager.active_connection_count == 0
        assert manager._connections == {}
        assert user_id not in manager._user_connections
        assert session_id not in manager._session_connections

    asyncio.run(scenario())


def test_unregister_unknown_connection_is_idempotent() -> None:
    """Removing an unknown identifier should return None without mutation."""

    async def scenario() -> None:
        manager = ConnectionManager()

        removed = await manager.unregister(uuid4())

        assert removed is None
        assert manager.active_connection_count == 0
        assert manager._connections == {}
        assert manager._user_connections == {}
        assert manager._session_connections == {}

    asyncio.run(scenario())


def test_concurrent_registration_preserves_every_connection() -> None:
    """Concurrent tasks should not lose primary or secondary-index entries."""

    async def scenario() -> None:
        manager = ConnectionManager()
        user_id = uuid4()
        session_id = uuid4()

        connections = await asyncio.gather(
            *(
                manager.register(
                    websocket=create_websocket(),
                    user_id=user_id,
                    session_id=session_id,
                )
                for _ in range(20)
            )
        )
        connection_ids = {connection.connection_id for connection in connections}

        assert len(connection_ids) == 20
        assert manager.active_connection_count == 20
        assert set(manager._connections) == connection_ids
        assert manager._user_connections[user_id] == connection_ids
        assert manager._session_connections[session_id] == connection_ids

    asyncio.run(scenario())


def test_close_session_closes_only_the_selected_session() -> None:
    """Session revocation should preserve another session for the same user."""

    async def scenario() -> None:
        manager = ConnectionManager()
        user_id = uuid4()
        selected_session_id = uuid4()
        other_session_id = uuid4()
        first_websocket = create_websocket()
        second_websocket = create_websocket()
        other_websocket = create_websocket()

        first = await manager.register(
            websocket=first_websocket,
            user_id=user_id,
            session_id=selected_session_id,
        )
        second = await manager.register(
            websocket=second_websocket,
            user_id=user_id,
            session_id=selected_session_id,
        )
        other = await manager.register(
            websocket=other_websocket,
            user_id=user_id,
            session_id=other_session_id,
        )

        closed_count = await manager.close_session(selected_session_id)

        assert closed_count == 2
        first_websocket.close.assert_awaited_once_with(
            code=WS_SESSION_REVOKED_CODE,
            reason=WS_SESSION_REVOKED_REASON,
        )
        second_websocket.close.assert_awaited_once_with(
            code=WS_SESSION_REVOKED_CODE,
            reason=WS_SESSION_REVOKED_REASON,
        )
        other_websocket.close.assert_not_awaited()
        assert manager.active_connection_count == 1
        assert set(manager._connections) == {other.connection_id}
        assert manager._user_connections[user_id] == {other.connection_id}
        assert selected_session_id not in manager._session_connections
        assert manager._session_connections[other_session_id] == {other.connection_id}
        assert await manager.unregister(first.connection_id) is None
        assert await manager.unregister(second.connection_id) is None

    asyncio.run(scenario())


def test_close_user_closes_every_user_session_and_preserves_other_users() -> None:
    """Logout-all should close all target-user sockets across device sessions."""

    async def scenario() -> None:
        manager = ConnectionManager()
        selected_user_id = uuid4()
        other_user_id = uuid4()
        first_websocket = create_websocket()
        second_websocket = create_websocket()
        other_websocket = create_websocket()
        first_session_id = uuid4()
        second_session_id = uuid4()
        other_session_id = uuid4()

        await manager.register(
            websocket=first_websocket,
            user_id=selected_user_id,
            session_id=first_session_id,
        )
        await manager.register(
            websocket=second_websocket,
            user_id=selected_user_id,
            session_id=second_session_id,
        )
        other = await manager.register(
            websocket=other_websocket,
            user_id=other_user_id,
            session_id=other_session_id,
        )

        closed_count = await manager.close_user(selected_user_id)

        assert closed_count == 2
        first_websocket.close.assert_awaited_once()
        second_websocket.close.assert_awaited_once()
        other_websocket.close.assert_not_awaited()
        assert manager.active_connection_count == 1
        assert selected_user_id not in manager._user_connections
        assert first_session_id not in manager._session_connections
        assert second_session_id not in manager._session_connections
        assert manager._user_connections[other_user_id] == {other.connection_id}
        assert manager._session_connections[other_session_id] == {other.connection_id}

    asyncio.run(scenario())


def test_close_operations_are_idempotent_for_unknown_identifiers() -> None:
    """Unknown users and sessions should close no sockets and return zero."""

    async def scenario() -> None:
        manager = ConnectionManager()

        assert await manager.close_session(uuid4()) == 0
        assert await manager.close_user(uuid4()) == 0
        assert manager.active_connection_count == 0
        assert manager._connections == {}
        assert manager._user_connections == {}
        assert manager._session_connections == {}

    asyncio.run(scenario())


def test_close_session_continues_after_one_websocket_close_fails(caplog) -> None:
    """One stale socket should not prevent cleanup of sibling connections."""

    async def scenario() -> None:
        manager = ConnectionManager()
        user_id = uuid4()
        session_id = uuid4()
        failed_websocket = create_websocket()
        healthy_websocket = create_websocket()
        failed_websocket.close = AsyncMock(
            side_effect=RuntimeError("socket already closed")
        )

        await manager.register(
            websocket=failed_websocket,
            user_id=user_id,
            session_id=session_id,
        )
        await manager.register(
            websocket=healthy_websocket,
            user_id=user_id,
            session_id=session_id,
        )

        closed_count = await manager.close_session(session_id)

        assert closed_count == 2
        failed_websocket.close.assert_awaited_once()
        healthy_websocket.close.assert_awaited_once()
        assert manager.active_connection_count == 0
        assert manager._connections == {}
        assert manager._user_connections == {}
        assert manager._session_connections == {}

    asyncio.run(scenario())

    assert "Failed to close WebSocket connection" in caplog.text


def test_close_all_closes_every_connection_with_service_restart_code() -> None:
    """Shutdown should close sockets across all users and device sessions."""

    async def scenario() -> None:
        manager = ConnectionManager()
        first_websocket = create_websocket()
        second_websocket = create_websocket()
        first = await manager.register(
            websocket=first_websocket,
            user_id=uuid4(),
            session_id=uuid4(),
        )
        second = await manager.register(
            websocket=second_websocket,
            user_id=uuid4(),
            session_id=uuid4(),
        )

        closed_count = await manager.close_all()

        assert closed_count == 2
        first_websocket.close.assert_awaited_once_with(
            code=WS_SERVICE_RESTART_CODE,
            reason=WS_SERVICE_RESTART_REASON,
        )
        second_websocket.close.assert_awaited_once_with(
            code=WS_SERVICE_RESTART_CODE,
            reason=WS_SERVICE_RESTART_REASON,
        )
        assert manager.active_connection_count == 0
        assert manager._connections == {}
        assert manager._user_connections == {}
        assert manager._session_connections == {}
        assert await manager.unregister(first.connection_id) is None
        assert await manager.unregister(second.connection_id) is None

    asyncio.run(scenario())


def test_close_all_is_idempotent_when_no_connections_exist() -> None:
    """Repeated or empty shutdown cleanup should return zero safely."""

    async def scenario() -> None:
        manager = ConnectionManager()

        assert await manager.close_all() == 0

        connection = await manager.register(
            websocket=create_websocket(),
            user_id=uuid4(),
            session_id=uuid4(),
        )
        assert await manager.close_all() == 1
        assert await manager.close_all() == 0
        assert await manager.unregister(connection.connection_id) is None

    asyncio.run(scenario())
