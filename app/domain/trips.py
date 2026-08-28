"""Trip and itinerary domain models."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.flights import FlightCabinClass
from app.domain.value_objects import CountryCode, CurrencyCode

MAX_TRAVELERS_PER_REQUEST = 100

ChildAge = Annotated[int, Field(ge=2, le=17)]
InfantAge = Annotated[int, Field(ge=0, le=1)]
Interest = Annotated[str, Field(min_length=1, max_length=60)]


class CanonicalLocation(BaseModel):
    """Provider-qualified location selected after ambiguity resolution."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    provider: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z0-9_-]+$",
    )
    provider_location_id: str = Field(min_length=1, max_length=256)
    canonical_name: str = Field(min_length=2, max_length=200)
    country_code: CountryCode
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        """Normalize provider namespaces before pattern validation."""

        if isinstance(value, str):
            return value.strip().lower()
        return value


class TravelerParty(BaseModel):
    """Travelers included in one trip-planning request."""

    model_config = ConfigDict(extra="forbid")

    adults: int = Field(default=1, ge=1, le=MAX_TRAVELERS_PER_REQUEST)
    children_ages: list[ChildAge] = Field(
        default_factory=list,
        max_length=MAX_TRAVELERS_PER_REQUEST,
    )
    infants_with_seat_ages: list[InfantAge] = Field(
        default_factory=list,
        max_length=MAX_TRAVELERS_PER_REQUEST,
    )
    infants_on_lap_ages: list[InfantAge] = Field(
        default_factory=list,
        max_length=MAX_TRAVELERS_PER_REQUEST,
    )

    @property
    def total_travelers(self) -> int:
        """Return the total number of travelers."""

        return (
            self.adults
            + len(self.children_ages)
            + len(self.infants_with_seat_ages)
            + len(self.infants_on_lap_ages)
        )

    @model_validator(mode="after")
    def validate_party(self) -> Self:
        """Validate traveler relationships and request size."""

        if len(self.infants_on_lap_ages) > self.adults:
            raise ValueError(
                "each lap infant must be accompanied by one adult; "
                "book additional infants with their own seat"
            )

        if self.total_travelers > MAX_TRAVELERS_PER_REQUEST:
            raise ValueError(
                f"traveler count cannot exceed {MAX_TRAVELERS_PER_REQUEST}"
            )

        return self


class TripRequest(BaseModel):
    """Normalized request for planning one trip."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
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

    travelers: TravelerParty = Field(default_factory=TravelerParty)
    rooms: int = Field(default=1, ge=1, le=100)

    interests: list[Interest] = Field(
        default_factory=list,
        max_length=10,
    )

    total_budget: Decimal | None = Field(
        default=None,
        gt=0,
    )
    budget_currency: CurrencyCode = "USD"

    cabin_class: FlightCabinClass = FlightCabinClass.ECONOMY
    nonstop_only: bool = False
    free_cancellation_only: bool = False

    @field_validator("interests")
    @classmethod
    def normalize_interests(cls, values: list[str]) -> list[str]:
        """Deduplicate interests while preserving their original order."""

        normalized: list[str] = []
        seen: set[str] = set()

        for value in values:
            interest = value.strip()
            key = interest.casefold()

            if key in seen:
                continue

            normalized.append(interest)
            seen.add(key)

        return normalized

    @model_validator(mode="after")
    def validate_trip(self) -> Self:
        """Validate trip dates, route, and room allocation."""

        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")

        if (
            self.origin is not None
            and self.origin.casefold() == self.destination.casefold()
        ):
            raise ValueError("origin and destination must be different")

        if self.rooms > self.travelers.adults:
            raise ValueError("each room requires at least one adult")

        return self


class TripStatus(StrEnum):
    """Lifecycle status persisted for a trip."""

    DRAFT = "draft"
    PLANNED = "planned"
    ARCHIVED = "archived"


class TripUpdate(BaseModel):
    """Validated partial changes for an existing trip."""

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
    destination: str | None = Field(
        default=None,
        min_length=2,
        max_length=120,
    )
    start_date: date | None = None
    end_date: date | None = None
    origin_location: CanonicalLocation | None = None
    destination_location: CanonicalLocation | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        """Reject empty updates, null required fields, and invalid date ranges."""

        if not self.model_fields_set:
            raise ValueError("at least one trip field must be provided")

        for field_name in {"destination", "start_date", "end_date"}:
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(f"{field_name} cannot be null")

        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date <= self.start_date
        ):
            raise ValueError("end_date must be after start_date")

        if (
            "origin" in self.model_fields_set
            and self.origin is None
            and self.origin_location is not None
        ):
            raise ValueError("origin_location requires origin")

        return self
