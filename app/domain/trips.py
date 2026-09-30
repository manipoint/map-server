"""Trip and itinerary domain models."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.flights import FlightCabinClass
from app.domain.trip_rules import (
    MAX_TRAVELERS_PER_REQUEST as MAX_TRAVELERS_PER_REQUEST,
)
from app.domain.trip_rules import (
    normalize_interests,
    validate_distinct_locations,
    validate_lap_infants,
    validate_room_allocation,
    validate_traveler_count,
    validate_trip_dates,
)
from app.domain.value_objects import (
    ChildAge as ChildAge,
)
from app.domain.value_objects import (
    CountryCode,
    CurrencyCode,
)
from app.domain.value_objects import (
    InfantAge as InfantAge,
)
from app.domain.value_objects import (
    Interest as Interest,
)


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

        validate_lap_infants(self.adults, len(self.infants_on_lap_ages))
        validate_traveler_count(self.total_travelers)

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

        return normalize_interests(values)

    @model_validator(mode="after")
    def validate_trip(self) -> Self:
        """Validate trip dates, route, and room allocation."""

        validate_trip_dates(self.start_date, self.end_date)
        validate_distinct_locations(self.origin, self.destination)
        validate_room_allocation(self.travelers.adults, self.rooms)

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

        validate_trip_dates(self.start_date, self.end_date)

        if (
            "origin" in self.model_fields_set
            and self.origin is None
            and self.origin_location is not None
        ):
            raise ValueError("origin_location requires origin")

        return self
