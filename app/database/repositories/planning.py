"""Ownership-scoped planning leases; no transaction spans a model call."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database.models.assistant_run import AssistantRun
from app.database.models.conversation import Conversation
from app.database.models.message import Message


class PlanningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def acquire(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        token: UUID,
        seconds: int,
        turn_number: int | None = None,
    ) -> tuple[dict[str, object], UUID | None] | None:
        statement = update(Conversation)
        if turn_number is not None:
            reply = aliased(Message)
            earlier = exists(
                select(Message.id)
                .outerjoin(AssistantRun, AssistantRun.user_message_id == Message.id)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.role == "user",
                    Message.turn_number < turn_number,
                    ~exists(
                        select(reply.id).where(reply.reply_to_message_id == Message.id)
                    ),
                    or_(
                        (AssistantRun.status == "processing")
                        & (AssistantRun.lease_expires_at > func.clock_timestamp()),
                        AssistantRun.id.is_(None)
                        & Message.generation_deferred.is_(False)
                        & (
                            Message.created_at
                            > func.clock_timestamp() - timedelta(seconds=seconds)
                        ),
                    ),
                )
            )
            statement = statement.where(~earlier)
        result = await self.session.execute(
            statement.where(
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
        release_lease: bool = True,
    ) -> None:
        """Stage owned planning state, optionally retaining the active lease."""
        values: dict[str, object] = {
            "planning_state": state,
            "planning_trip_id": trip_id,
        }
        if release_lease:
            values.update(
                planning_lease_token=None,
                planning_lease_expires_at=None,
            )
        result = await self.session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.planning_lease_token == token,
                Conversation.planning_lease_expires_at > func.clock_timestamp(),
            )
            .values(**values)
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
