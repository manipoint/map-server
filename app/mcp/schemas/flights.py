"""Flight-search MCP input schemas."""

from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.flights import FlightCabinClass


class FlightSearchInput(BaseModel):
    """Validated provider-independent flight-search request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    origin: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    destination: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    departure_date: date
    return_date: date | None = None

    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    infants_with_seat: int = Field(default=0, ge=0)
    infants_on_lap: int = Field(default=0, ge=0)

    cabin_class: FlightCabinClass = FlightCabinClass.ECONOMY
    nonstop_only: bool = False
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    max_results: int = Field(default=5, ge=1, le=10)

    @field_validator("origin", "destination", "currency", mode="before")
    @classmethod
    def normalize_codes(cls, value: object) -> object:
        """Normalize airport and currency codes before validation."""

        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def validate_search(self) -> Self:
        """Validate route, dates, and passenger relationships."""

        if self.origin == self.destination:
            raise ValueError("origin and destination must be different")

        if self.return_date is not None and self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")

        if self.infants_on_lap > self.adults:
            raise ValueError(
                "each lap infant must be accompanied by one adult; "
                "book additional infants with their own seat"
            )

        return self

    @property
    def total_travelers(self) -> int:
        """Return all travelers included in this search request."""

        return (
            self.adults + self.children + self.infants_with_seat + self.infants_on_lap
        )
