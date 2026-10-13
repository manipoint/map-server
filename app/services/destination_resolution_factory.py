"""Short-lived database scope for destination resolution operations."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.destinations import DestinationRepository
from app.services.country_recognition_service import CountryRecognitionService
from app.services.destination_resolution_service import DestinationResolutionService


class DestinationResolutionFactory:
    """Share the country index while creating repositories per operation."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        country_recognition: CountryRecognitionService,
    ) -> None:
        self.session_factory = session_factory
        self.country_recognition = country_recognition

    @asynccontextmanager
    async def open(
        self,
    ) -> AsyncGenerator[DestinationResolutionService, None]:
        """Close the session on success, failure, or cancellation."""
        async with self.session_factory() as session:
            yield DestinationResolutionService(
                repository=DestinationRepository(session=session),
                country_recognition=self.country_recognition,
            )
