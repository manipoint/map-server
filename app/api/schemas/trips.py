"""Public REST schemas for trip resources."""

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.trips import CanonicalLocation, TripStatus, TripUpdate


class TripCreateRequest(BaseModel):
    """Data required to create a draft trip."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )
    origin: str | None = Field(
        default=None,
        min_length=2,
        max_length=120,
    )
    destination: str = Field(
        min_length=2,
        max_length=120,
    )
    start_date: date
    end_date: date
    origin_location: CanonicalLocation | None = None
    destination_location: CanonicalLocation | None = None

    @model_validator(mode="after")
    def validate_trip_details(self) -> Self:
        """Validate the initial route and date range."""
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if (
            self.origin is not None
            and (
                self.origin_location.canonical_name
                if self.origin_location is not None
                else self.origin
            ).casefold()
            == (
                self.destination_location.canonical_name
                if self.destination_location is not None
                else self.destination
            ).casefold()
        ):
            raise ValueError("origin and destination must be different")

        if self.origin_location is not None and self.origin is None:
            raise ValueError("origin_location requires origin")

        return self


class TripResponse(BaseModel):
    """Public representation of one user-owned trip."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str | None
    origin: str | None
    destination: str
    origin_location: CanonicalLocation | None
    destination_location: CanonicalLocation | None
    start_date: date
    end_date: date
    status: TripStatus
    created_at: datetime
    updated_at: datetime


class TripListResponse(BaseModel):
    """Cursor-paginated trip collection."""

    items: list[TripResponse]
    next_cursor: str | None


class TripUpdateRequest(TripUpdate):
    """Public request for partially updating one trip."""
