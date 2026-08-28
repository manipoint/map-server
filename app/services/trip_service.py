"""Trip management use cases."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import (
    PageCursor,
    decode_page_cursor,
    encode_page_cursor,
)
from app.database.models.trip import Trip
from app.database.repositories.trips import TripRepository
from app.domain.errors import (
    InvalidTripDetailsError,
    InvalidTripStatusTransitionError,
    TripNotFoundError,
)
from app.domain.trips import CanonicalLocation, TripStatus, TripUpdate


@dataclass(frozen=True, slots=True)
class TripListResult:
    """One public page of user-owned trips."""

    items: list[Trip]
    next_cursor: str | None


class TripService:
    """Coordinate trip persistence and pagination rules."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        trip_repository: TripRepository | None = None,
    ) -> None:
        self.session = session
        self.trips = trip_repository or TripRepository(session)

    async def list_trips(
        self,
        *,
        user_id: UUID,
        status: TripStatus | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> TripListResult:
        """Return one cursor-paginated page of user-owned trips."""

        decoded_cursor = decode_page_cursor(cursor) if cursor is not None else None

        page = await self.trips.list_for_user(
            user_id=user_id,
            status=status,
            limit=limit,
            before_updated_at=(
                decoded_cursor.updated_at if decoded_cursor is not None else None
            ),
            before_id=(decoded_cursor.item_id if decoded_cursor is not None else None),
        )

        next_cursor: str | None = None

        if page.has_more:
            last_trip = page.items[-1]
            next_cursor = encode_page_cursor(
                PageCursor(
                    updated_at=last_trip.updated_at,
                    item_id=last_trip.id,
                )
            )

        return TripListResult(
            items=page.items,
            next_cursor=next_cursor,
        )

    async def create_trip(
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
        """Create and commit a new user-owned draft trip."""

        if origin is None and origin_location is not None:
            raise InvalidTripDetailsError("origin_location requires origin")
        resolved_origin = (
            origin_location.canonical_name if origin_location is not None else origin
        )
        resolved_destination = (
            destination_location.canonical_name
            if destination_location is not None
            else destination
        )
        if (
            resolved_origin is not None
            and resolved_origin.casefold() == resolved_destination.casefold()
        ):
            raise InvalidTripDetailsError("origin and destination must be different")

        try:
            trip = await self.trips.create(
                user_id=user_id,
                title=title,
                origin=origin,
                destination=destination,
                origin_location=origin_location,
                destination_location=destination_location,
                start_date=start_date,
                end_date=end_date,
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise

        return trip

    async def get_trip(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
    ) -> Trip:
        """Return one user-owned trip or raise a safe not-found error."""

        trip = await self.trips.get_by_id_for_user(trip_id=trip_id, user_id=user_id)
        if trip is None:
            raise TripNotFoundError("Trip was not found")
        return trip

    async def update_trip(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
        update: TripUpdate,
    ) -> Trip:
        """Merge, validate, and commit changes to a user-owned trip."""

        try:
            trip = await self.trips.get_by_id_for_user(
                trip_id=trip_id,
                user_id=user_id,
                for_update=True,
            )
            if trip is None:
                raise TripNotFoundError("Trip was not found")
            changed_fields = update.model_fields_set

            title = update.title if "title" in changed_fields else trip.title
            origin = update.origin if "origin" in changed_fields else trip.origin
            destination = (
                update.destination
                if "destination" in changed_fields
                else trip.destination
            )
            origin_location = self._merge_location(
                update=update,
                field_name="origin_location",
                text_field_name="origin",
                stored=trip.origin_location,
                stored_text=trip.origin,
            )
            destination_location = self._merge_location(
                update=update,
                field_name="destination_location",
                text_field_name="destination",
                stored=trip.destination_location,
                stored_text=trip.destination,
            )
            start_date = (
                update.start_date if "start_date" in changed_fields else trip.start_date
            )
            end_date = (
                update.end_date if "end_date" in changed_fields else trip.end_date
            )
            if destination is None or start_date is None or end_date is None:
                raise InvalidTripDetailsError("Required trip details cannot be null")
            if end_date <= start_date:
                raise InvalidTripDetailsError("end_date must be after start_date")

            if origin is not None and origin.casefold() == destination.casefold():
                raise InvalidTripDetailsError(
                    "origin and destination must be different"
                )
            if origin is None and origin_location is not None:
                raise InvalidTripDetailsError("origin_location requires origin")

            resolved_origin = (
                origin_location.canonical_name
                if origin_location is not None
                else origin
            )
            resolved_destination = (
                destination_location.canonical_name
                if destination_location is not None
                else destination
            )
            if (
                resolved_origin is not None
                and resolved_origin.casefold() == resolved_destination.casefold()
            ):
                raise InvalidTripDetailsError(
                    "origin and destination must be different"
                )
            updated_trip = await self.trips.update_details(
                trip=trip,
                title=title,
                origin=origin,
                destination=destination,
                start_date=start_date,
                end_date=end_date,
                origin_location=origin_location,
                destination_location=destination_location,
            )
            await self.session.commit()

        except BaseException:
            await self.session.rollback()
            raise

        return updated_trip

    @staticmethod
    def _merge_location(
        *,
        update: TripUpdate,
        field_name: str,
        text_field_name: str,
        stored: CanonicalLocation | None,
        stored_text: str | None,
    ) -> CanonicalLocation | None:
        """Merge metadata and clear it when its free-text endpoint changes."""

        if field_name in update.model_fields_set:
            return getattr(update, field_name)
        if text_field_name in update.model_fields_set:
            updated_text = getattr(update, text_field_name)
            if (
                updated_text is None
                or stored_text is None
                or updated_text.casefold() != stored_text.casefold()
            ):
                return None
        return stored

    async def mark_trip_planned(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
    ) -> Trip:
        """Finalize a draft trip as planned."""

        return await self._transition_status(
            trip_id=trip_id,
            user_id=user_id,
            target_status=TripStatus.PLANNED,
            allowed_sources={TripStatus.DRAFT},
        )

    async def archive_trip(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
    ) -> Trip:
        """Archive a draft or planned trip."""
        return await self._transition_status(
            trip_id=trip_id,
            user_id=user_id,
            target_status=TripStatus.ARCHIVED,
            allowed_sources={
                TripStatus.DRAFT,
                TripStatus.PLANNED,
            },
        )

    async def _transition_status(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
        target_status: TripStatus,
        allowed_sources: set[TripStatus],
    ) -> Trip:
        """Lock, validate, and commit one trip status transition."""
        try:
            trip = await self.trips.get_by_id_for_user(
                trip_id=trip_id, user_id=user_id, for_update=True
            )
            if trip is None:
                raise TripNotFoundError("Trip was not found")

            current_status = TripStatus(trip.status)
            if current_status == target_status:
                await self.session.commit()
                return trip
            if current_status not in allowed_sources:
                raise InvalidTripStatusTransitionError(
                    f"Trip cannot transition from "
                    f"{current_status.value} to {target_status.value}"
                )

            updated_trip = await self.trips.set_status(
                trip=trip,
                status=target_status,
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise

        return updated_trip

    async def delete_trip(
        self,
        *,
        trip_id: UUID,
        user_id: UUID,
    ) -> None:
        """Permanently delete one user-owned trip."""

        try:
            deleted = await self.trips.delete_by_id_for_user(
                trip_id=trip_id, user_id=user_id
            )
            if not deleted:
                raise TripNotFoundError("Trip was not found")

            await self.session.commit()

        except BaseException:
            await self.session.rollback()

            raise
