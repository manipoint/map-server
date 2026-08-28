"""Persistence operations for user-owned itineraries."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time import utc_now
from app.database.models import Itinerary, ItineraryItem, Trip
from app.domain.itineraries import (
    ItineraryItemDraft,
    ItineraryStatus,
)


@dataclass(frozen=True, slots=True)
class ItineraryDetails:
    """One itinerary version and its ordered timeline items."""

    itinerary: Itinerary
    items: list[ItineraryItem]


class ItineraryRepository:
    """Manage versioned itineraries through trip ownership."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_next_draft_for_user(
        self, *, trip_id: UUID, user_id: UUID
    ) -> Itinerary | None:
        """Create the next draft version for an owned trip."""

        trip = await self.get_owned_trip_with_lock(
            trip_id=trip_id,
            user_id=user_id,
        )
        if trip is None:
            return None

        return await self.create_next_draft_for_locked_trip(trip=trip)

    async def get_owned_trip_with_lock(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
    ) -> Trip | None:
        """Return an owned trip while serializing itinerary version changes."""

        statement = (
            select(Trip)
            .where(
                Trip.id == trip_id,
                Trip.user_id == user_id,
            )
            .with_for_update()
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_next_draft_for_locked_trip(
        self, *, trip: Trip, source_message_id: UUID | None = None
    ) -> Itinerary:
        """Create the next draft after the caller has locked the parent trip."""

        version_statement = select(
            func.coalesce(func.max(Itinerary.version), 0) + 1
        ).where(Itinerary.trip_id == trip.id)
        version_result = await self.session.execute(version_statement)
        next_version = version_result.scalar_one()

        itinerary = Itinerary(
            trip_id=trip.id,
            source_message_id=source_message_id,
            version=next_version,
            status=ItineraryStatus.DRAFT.value,
        )
        self.session.add(itinerary)
        await self.session.flush()
        return itinerary

    async def has_items(self, *, itinerary_id: UUID) -> bool:
        """Report whether an itinerary contains at least one timeline item."""

        statement = (
            select(ItineraryItem.id)
            .where(ItineraryItem.itinerary_id == itinerary_id)
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def add_items(
        self, *, itinerary: Itinerary, items: Sequence[ItineraryItemDraft]
    ) -> list[ItineraryItem]:
        """Add validated ordered items without committing."""

        if not items:
            return []

        itinerary_items = [
            ItineraryItem(
                itinerary_id=itinerary.id,
                day_number=item.day_number,
                position=item.position,
                item_type=item.item_type.value,
                title=item.title,
                description=item.description,
                location_name=item.location_name,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
            )
            for item in items
        ]
        self.session.add_all(itinerary_items)
        await self.session.flush()

        return itinerary_items

    async def get_details_for_user(
        self,
        *,
        itinerary_id: UUID,
        user_id: UUID,
    ) -> ItineraryDetails | None:
        """Return an owned itinerary with its ordered items."""

        itinerary_statement = (
            select(Itinerary)
            .join(
                Trip,
                Trip.id == Itinerary.trip_id,
            )
            .where(
                Itinerary.id == itinerary_id,
                Trip.user_id == user_id,
            )
        )
        itinerary_result = await self.session.execute(itinerary_statement)
        itinerary = itinerary_result.scalar_one_or_none()

        if itinerary is None:
            return None

        items = await self._list_items(itinerary_id=itinerary.id)

        return ItineraryDetails(
            itinerary=itinerary,
            items=items,
        )

    async def get_saved_details_for_trip_user(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
    ) -> ItineraryDetails | None:
        """Return the currently saved itinerary for an owned trip."""

        statement = (
            select(Itinerary)
            .join(
                Trip,
                Trip.id == Itinerary.trip_id,
            )
            .where(
                Itinerary.trip_id == trip_id,
                Itinerary.status == ItineraryStatus.SAVED.value,
                Trip.user_id == user_id,
            )
        )
        result = await self.session.execute(statement)
        itinerary = result.scalar_one_or_none()

        if itinerary is None:
            return None

        items = await self._list_items(itinerary_id=itinerary.id)

        return ItineraryDetails(
            itinerary=itinerary,
            items=items,
        )

    async def _list_items(
        self,
        *,
        itinerary_id: UUID,
    ) -> list[ItineraryItem]:
        """Return one itinerary's items in deterministic display order."""

        statement = (
            select(ItineraryItem)
            .where(ItineraryItem.itinerary_id == itinerary_id)
            .order_by(
                ItineraryItem.day_number.asc(),
                ItineraryItem.position.asc(),
                ItineraryItem.id.asc(),
            )
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_for_user_with_trip_lock(
        self,
        *,
        itinerary_id: UUID,
        user_id: UUID,
    ) -> Itinerary | None:
        """Return an owned itinerary while locking its parent trip."""

        statement = (
            select(Itinerary)
            .join(
                Trip,
                Trip.id == Itinerary.trip_id,
            )
            .where(
                Itinerary.id == itinerary_id,
                Trip.user_id == user_id,
            )
            .with_for_update(of=Trip)
        )

        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def replace_saved_with(
        self,
        *,
        itinerary: Itinerary,
    ) -> Itinerary:
        """Supersede the current saved version and save the supplied version."""

        updated_at = utc_now()

        supersede_statement = (
            update(Itinerary)
            .where(
                Itinerary.trip_id == itinerary.trip_id,
                Itinerary.id != itinerary.id,
                Itinerary.status == ItineraryStatus.SAVED.value,
            )
            .values(
                status=ItineraryStatus.SUPERSEDED.value,
                updated_at=updated_at,
            )
        )
        await self.session.execute(supersede_statement)

        itinerary.status = ItineraryStatus.SAVED.value
        itinerary.updated_at = updated_at
        await self.session.flush()

        return itinerary

    async def get_details_by_source_message(
        self, *, source_message_id: UUID, user_id: UUID
    ) -> ItineraryDetails | None:
        """Return an owned itinerary generated for one user message."""

        statement = (
            select(Itinerary)
            .join(Trip, Trip.id == Itinerary.trip_id)
            .where(
                Itinerary.source_message_id == source_message_id,
                Trip.user_id == user_id,
            )
        )
        result = await self.session.execute(statement=statement)
        itinerary = result.scalar_one_or_none()
        if itinerary is None:
            return None
        return ItineraryDetails(
            itinerary=itinerary,
            items=await self._list_items(
                itinerary_id=itinerary.id,
            ),
        )
