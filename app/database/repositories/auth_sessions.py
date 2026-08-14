"""Provide persistence operations for authentication sessions."""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.auth_session import AuthSession


class AuthSessionRepository:
    """Manage the persistence lifecycle of authentication sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(
        self,
        session_id: UUID,
    ) -> AuthSession | None:
        """Return an authentication session by its ID."""

        return await self.session.get(AuthSession, session_id)

    async def get_by_refresh_token_hash(
        self,
        token_hash: str,
        *,
        for_update: bool = False,
    ) -> AuthSession | None:
        """Return the session associated with a refresh-token hash."""

        statement = select(AuthSession).where(
            AuthSession.refresh_token_hash == token_hash
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: UUID,
        refresh_token_hash: str,
        device_id: str,
        device_name: str | None,
        expires_at: datetime,
        token_family_id: UUID | None = None,
        ip_address: IPv4Address | IPv6Address | None = None,
        user_agent: str | None = None,
    ) -> AuthSession:
        """Create and flush a new device authentication session."""

        auth_session = AuthSession(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            token_family_id=token_family_id or uuid4(),
            device_id=device_id,
            device_name=device_name,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        self.session.add(auth_session)
        await self.session.flush()
        return auth_session

    async def list_active_by_user(
        self,
        user_id: UUID,
        *,
        current_time: datetime,
    ) -> list[AuthSession]:
        """Return all active, unexpired sessions belonging to a user."""

        statement = (
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.rotated_at.is_(None),
                AuthSession.expires_at > current_time,
            )
            .order_by(AuthSession.created_at.desc())
        )

        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def revoke(
        self,
        auth_session: AuthSession,
        *,
        revoked_at: datetime,
        reason: str,
    ) -> bool:
        """Revoke one session without changing an earlier revocation."""

        if auth_session.revoked_at is not None:
            return False

        auth_session.revoked_at = revoked_at
        auth_session.revoke_reason = reason

        await self.session.flush()
        return True

    async def revoke_all_by_user(
        self,
        user_id: UUID,
        *,
        revoked_at: datetime,
        reason: str,
    ) -> int:
        """Revoke every active session belonging to a user."""

        statement = (
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.rotated_at.is_(None),
                AuthSession.expires_at > revoked_at,
            )
            .with_for_update()
        )

        result = await self.session.execute(statement)
        active_sessions = list(result.scalars().all())

        for auth_session in active_sessions:
            auth_session.revoked_at = revoked_at
            auth_session.revoke_reason = reason

        if active_sessions:
            await self.session.flush()

        return len(active_sessions)

    async def mark_rotated(
        self,
        auth_session: AuthSession,
        *,
        replacement_session_id: UUID,
        rotated_at: datetime,
    ) -> bool:
        """Mark a session as rotated and link its replacement."""

        if auth_session.rotated_at is not None or auth_session.revoked_at is not None:
            return False

        auth_session.rotated_at = rotated_at
        auth_session.replaced_by_session_id = replacement_session_id
        auth_session.last_used_at = rotated_at

        await self.session.flush()
        return True

    async def revoke_token_family(
        self,
        token_family_id: UUID,
        *,
        revoked_at: datetime,
        reason: str,
    ) -> int:
        """Revoke every unrevoked session in a refresh-token family."""
        statement = (
            select(AuthSession)
            .where(
                AuthSession.token_family_id == token_family_id,
                AuthSession.revoked_at.is_(None),
            )
            .order_by(AuthSession.created_at)
            .with_for_update()
        )
        result = await self.session.execute(statement)
        family_sessions = list(result.scalars().all())

        for auth_session in family_sessions:
            auth_session.revoked_at = revoked_at
            auth_session.revoke_reason = reason

        if family_sessions:
            await self.session.flush()

        return len(family_sessions)

    async def get_by_id_for_user(
        self, *, session_id: UUID, user_id: UUID, for_update: bool = False
    ) -> AuthSession | None:
        """Return a user-owned session, optionally locking its row."""
        statement = select(AuthSession).where(
            AuthSession.id == session_id, AuthSession.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
