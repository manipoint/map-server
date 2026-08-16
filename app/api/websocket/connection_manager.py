"""WebSocket connection management."""

import logging
from asyncio import Lock
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import WebSocket

logger = logging.getLogger(__name__)

WS_SESSION_REVOKED_CODE = 4401
WS_SERVICE_RESTART_CODE = 1012
WS_SESSION_REVOKED_REASON = "Authentication session revoked"
WS_SERVICE_RESTART_REASON = "Service restarting"


@dataclass(frozen=True, slots=True)
class ManagedConnection:
    """One authenticated WebSocket connection."""

    connection_id: UUID
    websocket: WebSocket
    user_id: UUID
    session_id: UUID
    connected_at: datetime


class ConnectionManager:
    """Track active WebSocket connections by user and auth session."""

    def __init__(self) -> None:
        self._connections: dict[UUID, ManagedConnection] = {}
        self._user_connections: dict[UUID, set[UUID]] = {}
        self._session_connections: dict[UUID, set[UUID]] = {}
        self._lock = Lock()

    @property
    def active_connection_count(self) -> int:
        """Return the number of currently registered connections."""

        return len(self._connections)

    async def register(
        self,
        *,
        websocket: WebSocket,
        user_id: UUID,
        session_id: UUID,
    ) -> ManagedConnection:
        """Register one authenticated WebSocket connection."""

        connection = ManagedConnection(
            connection_id=uuid4(),
            websocket=websocket,
            user_id=user_id,
            session_id=session_id,
            connected_at=datetime.now(UTC),
        )

        async with self._lock:
            self._connections[connection.connection_id] = connection
            self._user_connections.setdefault(
                user_id,
                set(),
            ).add(connection.connection_id)
            self._session_connections.setdefault(session_id, set()).add(
                connection.connection_id
            )

        return connection

    async def unregister(self, connection_id: UUID) -> ManagedConnection | None:
        """Remove one connection and clean its secondary indexes."""
        async with self._lock:
            return self._remove_connection(connection_id)

    def _remove_connection(
        self,
        connection_id: UUID,
    ) -> ManagedConnection | None:
        """Remove one connection while the manager lock is held."""
        connection = self._connections.pop(connection_id, None)
        if connection is None:
            return None
        self._remove_from_index(
            self._user_connections,
            connection.user_id,
            connection_id,
        )
        self._remove_from_index(
            self._session_connections,
            connection.session_id,
            connection_id,
        )
        return connection

    async def close_session(self, session_id: UUID) -> int:
        """Close every connection belonging to one authentication session."""
        return await self._close_indexed_connections(
            index=self._session_connections, key=session_id
        )

    async def close_user(self, user_id: UUID) -> int:
        """Close every connection belonging to one user."""
        return await self._close_indexed_connections(
            index=self._user_connections, key=user_id
        )

    async def _close_indexed_connections(
        self, *, index: dict[UUID, set[UUID]], key: UUID
    ) -> int:
        """Remove and close every connection referenced by an index key."""
        async with self._lock:
            connection_ids = tuple(index.get(key, set()))
            connections = [
                connection
                for connection_id in connection_ids
                if (connection := self._remove_connection(connection_id)) is not None
            ]
        return await self._close_connections(
            connections=connections,
            code=WS_SESSION_REVOKED_CODE,
            reason=WS_SESSION_REVOKED_REASON,
        )

    async def _close_connections(
        self, connections: list[ManagedConnection], *, code: int, reason: str
    ) -> int:
        """Close removed connections without holding the manager lock."""
        for connection in connections:
            try:
                await connection.websocket.close(code=code, reason=reason)
            except Exception:
                logger.warning(
                    "Failed to close WebSocket connection",
                    extra={
                        "connection_id": str(connection.connection_id),
                        "session_id": str(connection.session_id),
                    },
                    exc_info=True,
                )
        return len(connections)

    async def close_all(self) -> int:
        """Close every active connection during application shutdown."""
        async with self._lock:
            connection_ids = tuple(self._connections)
            connections = [
                connection
                for connection_id in connection_ids
                if (connection := self._remove_connection(connection_id)) is not None
            ]
        return await self._close_connections(
            connections, code=WS_SERVICE_RESTART_CODE, reason=WS_SERVICE_RESTART_REASON
        )

    @staticmethod
    def _remove_from_index(
        index: dict[UUID, set[UUID]], key: UUID, connection_id: UUID
    ) -> None:
        """Remove a connection ID and discard an empty index entry."""
        connection_ids = index.get(key)
        if connection_ids is None:
            return
        connection_ids.discard(connection_id)
        if not connection_ids:
            index.pop(key, None)
