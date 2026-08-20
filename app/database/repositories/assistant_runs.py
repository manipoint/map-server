"""Persistence operations for assistant processing leases."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.assistant_run import AssistantRun
from app.domain.enums import AssistantRunStatus


@dataclass(frozen=True, slots=True)
class AssistantRunClaim:
    """Result of trying to acquire an assistant processing lease."""

    run: AssistantRun
    acquired: bool
    claim_token: UUID | None
    observed_at: datetime


class AssistantRunRepository:
    """Manage atomic assistant processing leases."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def acquire_claim(
        self,
        *,
        user_message_id: UUID,
        lease_duration: timedelta,
        max_attempts: int,
    ) -> AssistantRunClaim:
        """Create or atomically reclaim a processing lease."""
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be greater than zero")
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")

        claim_token = uuid4()
        lease_expires_at = func.now() + lease_duration

        insert_statement = (
            insert(AssistantRun)
            .values(
                id=uuid4(),
                user_message_id=user_message_id,
                assistant_message_id=None,
                status=AssistantRunStatus.PROCESSING.value,
                claim_token=claim_token,
                lease_expires_at=lease_expires_at,
                attempt_count=1,
                last_error_code=None,
            )
            .on_conflict_do_nothing(
                constraint="uq_assistant_runs_user_message_id",
            )
            .returning(
                AssistantRun,
                func.now().label("observed_at"),
            )
        )
        insert_result = await self.session.execute(insert_statement)
        inserted_row = insert_result.one_or_none()

        if inserted_row is not None:
            run, observed_at = inserted_row
            return AssistantRunClaim(
                run=run,
                acquired=True,
                claim_token=claim_token,
                observed_at=observed_at,
            )
        reclaim_statement = (
            update(AssistantRun)
            .where(
                AssistantRun.user_message_id == user_message_id,
                AssistantRun.attempt_count < max_attempts,
                or_(
                    AssistantRun.status == AssistantRunStatus.FAILED.value,
                    and_(
                        AssistantRun.status == AssistantRunStatus.PROCESSING.value,
                        AssistantRun.lease_expires_at <= func.now(),
                    ),
                ),
            )
            .values(
                status=AssistantRunStatus.PROCESSING.value,
                claim_token=claim_token,
                lease_expires_at=lease_expires_at,
                assistant_message_id=None,
                last_error_code=None,
                attempt_count=AssistantRun.attempt_count + 1,
                updated_at=func.now(),
            )
            .returning(AssistantRun, func.now().label("observed_at"))
        )
        reclaim_result = await self.session.execute(reclaim_statement)
        reclaimed_row = reclaim_result.one_or_none()

        if reclaimed_row is not None:
            run, observed_at = reclaimed_row
            return AssistantRunClaim(
                run=run, acquired=True, claim_token=claim_token, observed_at=observed_at
            )
        existing_statement = select(
            AssistantRun, func.now().label("observed_at")
        ).where(
            AssistantRun.user_message_id == user_message_id,
        )
        existing_result = await self.session.execute(existing_statement)
        existing_row = existing_result.one_or_none()

        if existing_row is None:
            raise RuntimeError("Assistant run disappeared during claim acquisition")

        run, observed_at = existing_row
        return AssistantRunClaim(
            run=run,
            acquired=False,
            claim_token=None,
            observed_at=observed_at,
        )

    async def complete_run(
        self,
        *,
        run_id: UUID,
        claim_token: UUID,
        assistant_message_id: UUID,
    ) -> AssistantRun | None:
        """Complete a processing run only when the caller owns its claim."""
        statement = (
            update(AssistantRun)
            .where(
                AssistantRun.id == run_id,
                AssistantRun.status == AssistantRunStatus.PROCESSING.value,
                AssistantRun.claim_token == claim_token,
            )
            .values(
                status=AssistantRunStatus.COMPLETED.value,
                assistant_message_id=assistant_message_id,
                claim_token=None,
                lease_expires_at=None,
                last_error_code=None,
                updated_at=func.now(),
            )
            .returning(AssistantRun)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def fail_run(
        self, *, run_id: UUID, claim_token: UUID, error_code: str
    ) -> AssistantRun | None:
        """Fail a processing run only when the caller owns its claim."""
        normalized_error_code = error_code.strip()
        if not normalized_error_code:
            raise ValueError("error_code must not be blank")
        if len(normalized_error_code) > 64:
            raise ValueError("error_code must not exceed 64 characters")

        statement = (
            update(AssistantRun)
            .where(
                AssistantRun.id == run_id,
                AssistantRun.status == AssistantRunStatus.PROCESSING.value,
                AssistantRun.claim_token == claim_token,
            )
            .values(
                status=AssistantRunStatus.FAILED.value,
                assistant_message_id=None,
                claim_token=None,
                lease_expires_at=None,
                last_error_code=normalized_error_code,
                updated_at=func.now(),
            )
            .returning(AssistantRun)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
