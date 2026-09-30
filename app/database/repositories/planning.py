"""Ownership-scoped planning leases; no transaction spans a model call."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation import Conversation


class PlanningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def acquire(
        self, *, conversation_id: UUID, user_id: UUID, token: UUID, seconds: int
    ) -> tuple[dict[str, object], UUID | None] | None:
        result = await self.session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                or_(
                    Conversation.planning_lease_token.is_(None),
                    Conversation.planning_lease_expires_at <= func.clock_timestamp(),
                ),
            )
            .values(
                planning_lease_token=token,
                planning_lease_expires_at=func.clock_timestamp()
                + timedelta(seconds=seconds),
            )
            .returning(Conversation.planning_state, Conversation.planning_trip_id)
            .execution_options(synchronize_session=False)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def stage(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        token: UUID,
        state: dict[str, object],
        trip_id: UUID | None,
    ) -> None:
        result = await self.session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.planning_lease_token == token,
                Conversation.planning_lease_expires_at > func.clock_timestamp(),
            )
            .values(
                planning_state=state,
                planning_trip_id=trip_id,
                planning_lease_token=None,
                planning_lease_expires_at=None,
            )
            .returning(Conversation.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            raise RuntimeError("Planning lease is no longer owned")

    async def release(
        self, *, conversation_id: UUID, user_id: UUID, token: UUID
    ) -> None:
        await self.session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.planning_lease_token == token,
            )
            .values(planning_lease_token=None, planning_lease_expires_at=None)
            .execution_options(synchronize_session=False)
        )
