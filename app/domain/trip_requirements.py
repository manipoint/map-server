"""Partial trip requirements collected across conversation turns."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from app.domain.flights import FlightCabinClass
from app.domain.preferences import TripPace
from app.domain.trip_rules import (
    MAX_TRAVELERS_PER_REQUEST,
    end_date_from_duration,
    inclusive_day_count,
    normalize_interests,
    validate_distinct_locations,
    validate_lap_infants,
    validate_room_allocation,
    validate_traveler_count,
    validate_trip_dates,
)
from app.domain.value_objects import CurrencyCode, Interest, StrictMinorAge


class TripTransport(StrEnum):
    FLIGHT = "flight"
    ROAD = "road"
    RAIL = "rail"
    OWN_ARRANGEMENTS = "own_arrangements"


class BudgetDecision(StrEnum):
    SPECIFIED = "specified"
    UNDECIDED = "undecided"
    NO_LIMIT = "no_limit"


class TripRequirements(BaseModel):
    """Validated partial state, not a provider search request."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        hide_input_in_errors=True,
        frozen=True,
    )

    destination: str | None = Field(
        default=None,
        min_length=2,
        max_length=120,
    )
    origin: str | None = Field(
        default=None,
        min_length=2,
        max_length=120,
    )

    start_date: date | None = None
    end_date: date | None = None
    date_window_start: date | None = None
    date_window_end: date | None = None
    duration_days: int | None = Field(
        default=None,
        ge=2,
        strict=True,
    )

    adults: int | None = Field(
        default=None,
        ge=1,
        le=MAX_TRAVELERS_PER_REQUEST,
        strict=True,
    )
    minor_count: int | None = Field(
        default=None,
        ge=0,
        le=MAX_TRAVELERS_PER_REQUEST - 1,
        strict=True,
    )
    minor_ages: tuple[StrictMinorAge, ...] | None = Field(
        default=None,
        max_length=MAX_TRAVELERS_PER_REQUEST - 1,
    )

    transport: TripTransport | None = None
    cabin_class: FlightCabinClass | None = None

    # One entry per infant, in the order infants occur in minor_ages.
    # True means on lap; False means occupying a seat.
    infant_on_lap: tuple[StrictBool, ...] | None = Field(
        default=None,
        max_length=MAX_TRAVELERS_PER_REQUEST - 1,
    )

    needs_lodging: bool | None = Field(default=None, strict=True)
    rooms: int | None = Field(
        default=None,
        ge=1,
        le=MAX_TRAVELERS_PER_REQUEST,
        strict=True,
    )

    budget_decision: BudgetDecision | None = None
    total_budget: Decimal | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
    budget_currency: CurrencyCode | None = None

    interests: tuple[Interest, ...] = Field(
        default_factory=tuple,
        max_length=10,
    )
    trip_pace: TripPace | None = None
    constraints: tuple[Annotated[str, Field(min_length=1, max_length=300)], ...] = (
        Field(
            default=(),
            max_length=10,
            description="Explicit accessibility, dietary, exclusion and other trip requirements.",
        )
    )

    @field_validator("interests")
    @classmethod
    def deduplicate_interests(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_interests(values))

    @property
    def resolved_end_date(self) -> date | None:
        """Use an explicit end date or derive it from inclusive duration."""
        if self.end_date is not None:
            return self.end_date
        if self.start_date is not None and self.duration_days is not None:
            return end_date_from_duration(self.start_date, self.duration_days)
        return None

    @model_validator(mode="after")
    def validate_known_requirements(self) -> Self:
        """Reject contradictions without requiring missing information."""
        validate_trip_dates(self.start_date, self.resolved_end_date)
        if self.start_date is not None and self.end_date is not None:
            actual_days = inclusive_day_count(self.start_date, self.end_date)

            if self.duration_days is not None and self.duration_days != actual_days:
                raise ValueError("duration_days must match the inclusive date range")

        validate_distinct_locations(self.origin, self.destination)

        exact_dates_present = self.start_date is not None or self.end_date is not None
        window_dates_present = (
            self.date_window_start is not None or self.date_window_end is not None
        )
        if exact_dates_present and window_dates_present:
            raise ValueError("exact dates and flexible window cannot be mixed")
        if self.date_window_start is not None and self.date_window_end is not None:
            validate_trip_dates(self.date_window_start, self.date_window_end)

            window_days = inclusive_day_count(
                self.date_window_start, self.date_window_end
            )
            if window_days > 31:
                raise ValueError("flexible date window cannot exceed 31 days")
            if self.duration_days is not None and self.duration_days > window_days:
                raise ValueError("trip duration cannot exceed its date window")

        if (
            self.minor_count is not None
            and self.minor_ages is not None
            and len(self.minor_ages) != self.minor_count
        ):
            raise ValueError("minor_ages must match minor_count")

        known_minor_count = (
            self.minor_count
            if self.minor_count is not None
            else (len(self.minor_ages) if self.minor_ages is not None else 0)
        )

        if self.adults is not None:
            validate_traveler_count(self.adults + known_minor_count)

        if self.infant_on_lap is not None:
            if self.minor_ages is None:
                raise ValueError("infant seating requires known minor ages")

            infant_count = sum(age < 2 for age in self.minor_ages)

            if len(self.infant_on_lap) != infant_count:
                raise ValueError("infant seating must cover every infant")

            validate_lap_infants(self.adults, sum(self.infant_on_lap))

        if self.needs_lodging is True:
            validate_room_allocation(self.adults, self.rooms)

        if (
            self.budget_decision in {BudgetDecision.UNDECIDED, BudgetDecision.NO_LIMIT}
            and self.total_budget is not None
        ):
            raise ValueError("a budget amount requires the specified budget decision")

        return self
