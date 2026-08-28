"""Bounded active-trip context supplied to the travel graph."""

from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActiveTripContext(BaseModel):
    """Trusted trip details required for itinerary generation."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    origin: str | None = Field(default=None, max_length=120)
    destination: str = Field(min_length=2, max_length=120)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_date_order(self) -> Self:
        """Require the trip to end after it starts."""

        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self

    @property
    def day_count(self) -> int:
        """Return inclusive trip duration."""

        return (self.end_date - self.start_date).days + 1
