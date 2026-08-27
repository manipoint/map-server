"""Public REST schemas for trip resources."""

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.trips import TripStatus, TripUpdate


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

    @model_validator(mode="after")
    def validate_trip_details(self) -> Self:
        """Validate the initial route and date range."""
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if (
            self.origin is not None
            and self.origin.casefold() == self.destination.casefold()
        ):
            raise ValueError("origin and destination must be different")

        return self


class TripResponse(BaseModel):
    """Public representation of one user-owned trip."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str | None
    origin: str | None
    destination: str
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
