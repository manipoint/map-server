"""Normalized flight-provider response models."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.domain.flights import FlightCabinClass, FlightSearchStatus

ChildAge = Annotated[int, Field(ge=2, le=17)]
InfantAge = Annotated[int, Field(ge=0, le=1)]


class FlightSearchInput(BaseModel):
    """Validated provider-independent flight-search request."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    origin: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    destination: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    departure_date: date
    return_date: date | None = None

    adults: int = Field(default=1, ge=1)
    children_ages: list[ChildAge] = Field(default_factory=list)
    infants_with_seat_ages: list[InfantAge] = Field(default_factory=list)
    infants_on_lap_ages: list[InfantAge] = Field(default_factory=list)
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
    def normalize_search_codes(cls, value: object) -> object:
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

        if len(self.infants_on_lap_ages) > self.adults:
            raise ValueError(
                "each lap infant must be accompanied by one adult; "
                "book additional infants with their own seat"
            )
        return self

    @property
    def total_travelers(self) -> int:
        """Return all travelers included in this search request."""

        return (
            self.adults
            + len(self.children_ages)
            + len(self.infants_with_seat_ages)
            + len(self.infants_on_lap_ages)
        )


class FlightSegment(BaseModel):
    """One direct flight leg between two airports."""

    model_config = ConfigDict(str_strip_whitespace=True)

    departure_airport: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    arrival_airport: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )

    departure_at: datetime
    arrival_at: datetime
    departure_time_zone: str = Field(min_length=1, max_length=64)
    arrival_time_zone: str = Field(min_length=1, max_length=64)
    marketing_carrier_code: str = Field(
        min_length=2,
        max_length=3,
        pattern=r"^[A-Z0-9]{2,3}$",
    )
    marketing_carrier_name: str = Field(min_length=1, max_length=120)
    marketing_flight_number: str = Field(
        min_length=1,
        max_length=8,
        pattern=r"^[A-Z0-9]{1,8}$",
    )

    operating_carrier_code: str = Field(
        min_length=2,
        max_length=3,
        pattern=r"^[A-Z0-9]{2,3}$",
    )
    operating_carrier_name: str = Field(min_length=1, max_length=120)
    operating_flight_number: str = Field(
        min_length=1,
        max_length=8,
        pattern=r"^[A-Z0-9]{1,8}$",
    )
    duration_minutes: int = Field(ge=1)

    @field_validator(
        "departure_airport",
        "arrival_airport",
        "marketing_carrier_code",
        "marketing_flight_number",
        "operating_carrier_code",
        "operating_flight_number",
        mode="before",
    )
    @classmethod
    def normalize_codes(cls, value: object) -> object:
        """Normalize provider codes before validation."""

        if isinstance(value, str):
            return value.strip().upper()

        return value

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        """Validate airport route and comparable scheduled times."""

        if self.departure_airport == self.arrival_airport:
            raise ValueError("segment airports must be different")

        departure_offset = self.departure_at.utcoffset()
        arrival_offset = self.arrival_at.utcoffset()

        if (departure_offset is None) != (arrival_offset is None):
            raise ValueError(
                "departure_at and arrival_at must use consistent timezone information"
            )

        if (
            departure_offset is not None
            and arrival_offset is not None
            and self.arrival_at <= self.departure_at
        ):
            raise ValueError("arrival_at must be after departure_at")

        return self


class FlightItinerary(BaseModel):
    """One outbound or return itinerary containing flight segments."""

    segments: list[FlightSegment] = Field(min_length=1)
    duration_minutes: int = Field(ge=1)

    @computed_field
    @property
    def stops(self) -> int:
        """Return the number of connections."""

        return len(self.segments) - 1


class FlightOffer(BaseModel):
    """One normalized offer whose price covers all travelers."""

    model_config = ConfigDict(str_strip_whitespace=True)

    offer_id: str = Field(min_length=1, max_length=256)
    outbound: FlightItinerary
    return_itinerary: FlightItinerary | None = None
    total_price: Decimal = Field(ge=0)
    currency: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    traveler_count: int = Field(ge=1)
    seats_available: int | None = Field(default=None, ge=0)
    refundable: bool | None = None
    expires_at: datetime | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        """Normalize the offer currency."""

        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expiry_timezone(cls, value: datetime | None) -> datetime | None:
        """Require an absolute instant when an offer expiry is provided."""

        if value is not None and value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value


class FlightSearchResult(BaseModel):
    """Normalized flight-search result, including group-booking outcomes."""

    model_config = ConfigDict(str_strip_whitespace=True)

    status: FlightSearchStatus
    searched_at: datetime
    offers: list[FlightOffer] = Field(default_factory=list, max_length=10)
    message: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        """Keep result status consistent with offers and user guidance."""
        if self.searched_at.utcoffset() is None:
            raise ValueError("searched_at must include a timezone")

        if self.status is FlightSearchStatus.OFFERS_AVAILABLE and not self.offers:
            raise ValueError("offers_available requires at least one offer")

        if self.status is not FlightSearchStatus.OFFERS_AVAILABLE and self.offers:
            raise ValueError("non-offer search results cannot contain offers")

        if (
            self.status is FlightSearchStatus.GROUP_BOOKING_REQUIRED
            and not self.message
        ):
            raise ValueError("group_booking_required requires user guidance")

        return self
