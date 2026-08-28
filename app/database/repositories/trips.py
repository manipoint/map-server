"""Provide persistence operations for user-owned trips."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time import utc_now
from app.database.models.trip import Trip
from app.domain.trips import CanonicalLocation, TripStatus


@dataclass(frozen=True, slots=True)
class TripPage:
    """One keyset-paginated collection of trips."""

    items: list[Trip]
    has_more: bool


class TripRepository:
    """Manage travel plans owned by authenticated users."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        destination: str,
        start_date: date,
        end_date: date,
        title: str | None = None,
        origin: str | None = None,
        origin_location: CanonicalLocation | None = None,
        destination_location: CanonicalLocation | None = None,
    ) -> Trip:
        """Create and flush a draft trip without committing."""

        trip = Trip(
            user_id=user_id,
            title=title,
            origin=origin,
            destination=destination,
            **self._location_columns("origin", origin_location),
            **self._location_columns("destination", destination_location),
            start_date=start_date,
            end_date=end_date,
            status=TripStatus.DRAFT.value,
        )
        self.session.add(trip)
        await self.session.flush()
        return trip

    async def get_by_id_for_user(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> Trip | None:
        """Return a user-owned trip, optionally locking its row."""

        statement = select(Trip).where(
            Trip.id == trip_id,
            Trip.user_id == user_id,
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        status: TripStatus | None = None,
        limit: int = 20,
        before_updated_at: datetime | None = None,
        before_id: UUID | None = None,
    ) -> TripPage:
        """Return one cursor-based page of user-owned trips."""

        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        if (before_updated_at is None) != (before_id is None):
            raise ValueError(
                "before_updated_at and before_id must be provided together"
            )

        statement = select(Trip).where(Trip.user_id == user_id)

        if status is not None:
            statement = statement.where(Trip.status == status.value)

        if before_updated_at is not None and before_id is not None:
            statement = statement.where(
                or_(
                    Trip.updated_at < before_updated_at,
                    and_(
                        Trip.updated_at == before_updated_at,
                        Trip.id < before_id,
                    ),
                )
            )

        statement = statement.order_by(
            Trip.updated_at.desc(),
            Trip.id.desc(),
        ).limit(limit + 1)

        result = await self.session.execute(statement)
        trips = list(result.scalars().all())

        return TripPage(items=trips[:limit], has_more=len(trips) > limit)

    async def update_details(
        self,
        *,
        trip: Trip,
        title: str | None,
        origin: str | None,
        destination: str,
        start_date: date,
        end_date: date,
        origin_location: CanonicalLocation | None,
        destination_location: CanonicalLocation | None,
    ) -> Trip:
        """Replace editable trip details and flush without committing."""

        trip.title = title
        trip.origin = origin
        trip.destination = destination
        trip.start_date = start_date
        trip.end_date = end_date
        self._assign_location(trip, "origin", origin_location)
        self._assign_location(trip, "destination", destination_location)
        trip.updated_at = utc_now()

        await self.session.flush()
        return trip

    @staticmethod
    def _location_columns(
        prefix: str,
        location: CanonicalLocation | None,
    ) -> dict[str, object | None]:
        """Map one atomic domain location to flat persistence fields."""

        values = (
            {
                "location_provider": location.provider,
                "provider_location_id": location.provider_location_id,
                "canonical_name": location.canonical_name,
                "country_code": location.country_code,
                "latitude": location.latitude,
                "longitude": location.longitude,
            }
            if location is not None
            else {
                "location_provider": None,
                "provider_location_id": None,
                "canonical_name": None,
                "country_code": None,
                "latitude": None,
                "longitude": None,
            }
        )
        return {f"{prefix}_{name}": value for name, value in values.items()}

    @classmethod
    def _assign_location(
        cls,
        trip: Trip,
        prefix: str,
        location: CanonicalLocation | None,
    ) -> None:
        """Replace all columns for one location as a single unit."""

        for field_name, value in cls._location_columns(prefix, location).items():
            setattr(trip, field_name, value)

    async def set_status(
        self,
        *,
        trip: Trip,
        status: TripStatus,
    ) -> Trip:
        """Set a trip status and flush without committing."""

        trip.status = status.value
        trip.updated_at = utc_now()
        await self.session.flush()
        return trip

    async def delete_by_id_for_user(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Delete a user-owned trip and report whether it existed."""
        statement = (
            delete(Trip)
            .where(
                Trip.id == trip_id,
                Trip.user_id == user_id,
            )
            .returning(Trip.id)
        )
        result = await self.session.execute(statement=statement)
        return result.scalar_one_or_none() is not None
