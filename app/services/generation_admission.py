"""Short database transactions bound work and spending across workers."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.database.models.generation_limits import GenerationLease, GenerationUsage
from app.domain.enums import TravelResponseErrorCode

logger = logging.getLogger(__name__)


class GenerationAlreadyInProgressError(Exception):
    """This user's message already holds an active generation reservation."""


class GenerationAdmissionError(Exception):
    def __init__(self, code: TravelResponseErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class GenerationAdmission:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.sessions = session_factory
        self.settings = settings

    @asynccontextmanager
    async def reserve(
        self,
        user_id: UUID,
        *,
        message_id: UUID,
    ) -> AsyncGenerator[None]:
        token = uuid4()
        lease_seconds = self.settings.assistant_run_lease_seconds * 2
        deadline = asyncio.get_running_loop().time() + lease_seconds - 5

        async with self.sessions() as session, session.begin():
            await session.execute(select(func.pg_advisory_xact_lock(746219031)))
            now = await session.scalar(select(func.clock_timestamp()))
            if now is None:
                raise RuntimeError("Database clock is unavailable")

            today = now.astimezone(UTC).date()
            await session.execute(
                delete(GenerationLease).where(GenerationLease.expires_at <= now)
            )

            existing_token = await session.scalar(
                select(GenerationLease.token).where(
                    GenerationLease.user_id == user_id,
                    GenerationLease.message_id == message_id,
                )
            )
            if existing_token is not None:
                raise GenerationAlreadyInProgressError()

            total, own = (
                await session.execute(
                    select(
                        func.count(),
                        func.count().filter(GenerationLease.user_id == user_id),
                    ).select_from(GenerationLease)
                )
            ).one()

            if (
                total >= self.settings.generation_global_concurrency
                or own >= self.settings.generation_user_concurrency
            ):
                raise GenerationAdmissionError(
                    TravelResponseErrorCode.CAPACITY_EXCEEDED
                )

            usage = await session.get(
                GenerationUsage,
                (user_id, today),
            )
            if (
                usage is not None
                and usage.requests >= self.settings.generation_daily_request_limit
            ):
                raise GenerationAdmissionError(
                    TravelResponseErrorCode.DAILY_LIMIT_EXCEEDED
                )

            await session.execute(
                delete(GenerationUsage).where(
                    GenerationUsage.user_id == user_id,
                    GenerationUsage.day < today - timedelta(days=7),
                )
            )

            if usage is None:
                session.add(
                    GenerationUsage(
                        user_id=user_id,
                        day=today,
                        requests=1,
                    )
                )
            else:
                usage.requests += 1

            session.add(
                GenerationLease(
                    token=token,
                    user_id=user_id,
                    message_id=message_id,
                    expires_at=now + timedelta(seconds=lease_seconds),
                )
            )

        try:
            async with asyncio.timeout_at(deadline):
                yield
        finally:
            try:
                async with asyncio.timeout(5):
                    async with self.sessions() as session, session.begin():
                        await session.execute(
                            delete(GenerationLease).where(
                                GenerationLease.token == token,
                                GenerationLease.user_id == user_id,
                            )
                        )
            except Exception as error:
                # Expiry recovers capacity if cleanup fails.
                logger.warning(
                    "Generation admission cleanup failed",
                    extra={"error_type": type(error).__name__},
                )
