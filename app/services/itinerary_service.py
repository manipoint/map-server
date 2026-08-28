"""Versioned itinerary management use cases."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.itineraries import (
    ItineraryDetails,
    ItineraryRepository,
)
from app.domain.errors import (
    InvalidItineraryDetailsError,
    InvalidItineraryStatusTransitionError,
    ItineraryNotFoundError,
    TripNotFoundError,
)
from app.domain.itineraries import ItineraryItemDraft, ItineraryStatus


class ItineraryService:
    """Coordinate itinerary validation, versioning, and persistence."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        itinerary_repository: ItineraryRepository | None = None,
    ) -> None:
        self.session = session
        self.itineraries = itinerary_repository or ItineraryRepository(session)

    async def create_draft(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
        items: Sequence[ItineraryItemDraft],
    ) -> ItineraryDetails:
        """Create and commit the next complete draft for an owned trip."""

        self._validate_item_positions(items)

        try:
            trip = await self.itineraries.get_owned_trip_with_lock(
                trip_id=trip_id,
                user_id=user_id,
            )
            if trip is None:
                raise TripNotFoundError("Trip was not found")

            trip_day_count = (trip.end_date - trip.start_date).days + 1
            if any(item.day_number > trip_day_count for item in items):
                raise InvalidItineraryDetailsError(
                    "An itinerary item falls outside the trip date range"
                )

            itinerary = await self.itineraries.create_next_draft_for_locked_trip(
                trip=trip
            )
            created_items = await self.itineraries.add_items(
                itinerary=itinerary,
                items=items,
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise

        return ItineraryDetails(
            itinerary=itinerary,
            items=created_items,
        )

    async def get_itinerary(
        self,
        *,
        itinerary_id: UUID,
        user_id: UUID,
    ) -> ItineraryDetails:
        """Return one owned itinerary version."""

        details = await self.itineraries.get_details_for_user(
            itinerary_id=itinerary_id,
            user_id=user_id,
        )
        if details is None:
            raise ItineraryNotFoundError("Itinerary was not found")
        return details

    async def find_generated_draft(
        self,
        *,
        source_message_id: UUID,
        user_id: UUID,
    ) -> ItineraryDetails | None:
        """Return a generated draft when one exists for the user message."""

        return await self.itineraries.get_details_by_source_message(
            source_message_id=source_message_id,
            user_id=user_id,
        )

    async def get_saved_itinerary(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
    ) -> ItineraryDetails:
        """Return the current saved itinerary for an owned trip."""

        details = await self.itineraries.get_saved_details_for_trip_user(
            trip_id=trip_id,
            user_id=user_id,
        )
        if details is None:
            raise ItineraryNotFoundError("Saved itinerary was not found")
        return details

    async def save_itinerary(
        self,
        *,
        itinerary_id: UUID,
        user_id: UUID,
    ) -> ItineraryDetails:
        """Make one draft the trip's current saved itinerary."""

        try:
            itinerary = await self.itineraries.get_for_user_with_trip_lock(
                itinerary_id=itinerary_id,
                user_id=user_id,
            )
            if itinerary is None:
                raise ItineraryNotFoundError("Itinerary was not found")

            current_status = ItineraryStatus(itinerary.status)
            if current_status == ItineraryStatus.SUPERSEDED:
                raise InvalidItineraryStatusTransitionError(
                    "A superseded itinerary cannot become saved"
                )

            if not await self.itineraries.has_items(itinerary_id=itinerary.id):
                raise InvalidItineraryDetailsError(
                    "An empty itinerary cannot become saved"
                )

            if current_status == ItineraryStatus.DRAFT:
                await self.itineraries.replace_saved_with(itinerary=itinerary)

            details = await self.itineraries.get_details_for_user(
                itinerary_id=itinerary.id,
                user_id=user_id,
            )
            if details is None:  # Defensive: ownership was verified under lock.
                raise ItineraryNotFoundError("Itinerary was not found")

            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise

        return details

    @staticmethod
    def _validate_item_positions(items: Sequence[ItineraryItemDraft]) -> None:
        """Require a non-empty timeline with unique day positions."""

        if not items:
            raise InvalidItineraryDetailsError(
                "An itinerary must contain at least one item"
            )

        positions = {(item.day_number, item.position) for item in items}
        if len(positions) != len(items):
            raise InvalidItineraryDetailsError(
                "Itinerary item positions must be unique within each day"
            )

    async def create_generated_draft(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
        source_message_id: UUID,
        items: Sequence[ItineraryItemDraft],
    ) -> ItineraryDetails:
        """Create or reuse the generated draft for one user message."""

        self._validate_item_positions(items=items)

        try:
            existing = await self.itineraries.get_details_by_source_message(
                source_message_id=source_message_id, user_id=user_id
            )
            if existing is not None:
                return existing
            trip = await self.itineraries.get_owned_trip_with_lock(
                trip_id=trip_id, user_id=user_id
            )
            if trip is None:
                raise TripNotFoundError("Trip was not found")

            existing = await self.itineraries.get_details_by_source_message(
                source_message_id=source_message_id,
                user_id=user_id,
            )
            if existing is not None:
                await self.session.rollback()
                return existing
            trip_day_count = (trip.end_date - trip.start_date).days + 1
            if any(item.day_number > trip_day_count for item in items):
                raise InvalidItineraryDetailsError(
                    "An itinerary item falls outside the trip date range"
                )
            itinerary = await self.itineraries.create_next_draft_for_locked_trip(
                trip=trip, source_message_id=source_message_id
            )
            created_items = await self.itineraries.add_items(
                itinerary=itinerary,
                items=items,
            )
            await self.session.commit()

        except BaseException:
            await self.session.rollback()
            raise

        return ItineraryDetails(itinerary=itinerary, items=created_items)
