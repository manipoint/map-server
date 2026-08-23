"""Validated Duffel API request and response schemas."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.flights import FlightCabinClass

DuffelPassengerType = Literal[
    "adult",
    "child",
    "infant_without_seat",
]


class DuffelPassenger(BaseModel):
    """One passenger represented by either type or exact age."""

    model_config = ConfigDict(extra="forbid")

    type: DuffelPassengerType | None = None
    age: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Require exactly one passenger classification."""
        has_type = self.type is not None
        has_age = self.age is not None

        if has_type == has_age:
            raise ValueError("Duffel passenger requires exactly one of type or age")

        return self


class DuffelSlice(BaseModel):
    """One outbound or return journey requested from Duffel."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    origin: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    destination: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    departure_date: date

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def normalize_airport_codes(cls, value: object) -> object:
        """Normalize IATA codes before validation."""

        if isinstance(value, str):
            return value.strip().upper()

        return value

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        """Require different origin and destination codes."""

        if self.origin == self.destination:
            raise ValueError("Duffel slice airports must be different")

        return self


class DuffelOfferRequestData(BaseModel):
    """Duffel offer-search request body data."""

    model_config = ConfigDict(extra="forbid")

    slices: list[DuffelSlice] = Field(
        min_length=1,
        max_length=2,
    )
    passengers: list[DuffelPassenger] = Field(min_length=1)
    cabin_class: FlightCabinClass
    max_connections: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_round_trip(self) -> Self:
        """Require a chronological reverse slice for a round trip."""

        if len(self.slices) == 1:
            return self

        outbound, inbound = self.slices
        if (
            inbound.origin != outbound.destination
            or inbound.destination != outbound.origin
        ):
            raise ValueError("Duffel return slice must reverse the outbound route")

        if inbound.departure_date < outbound.departure_date:
            raise ValueError("Duffel return date must not precede departure date")

        return self


class DuffelOfferRequestPayload(BaseModel):
    """Top-level Duffel API request envelope."""

    model_config = ConfigDict(extra="forbid")

    data: DuffelOfferRequestData


class DuffelResponseModel(BaseModel):
    """Base model tolerant of new Duffel response fields."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )


class DuffelOfferRequestReference(DuffelResponseModel):
    """Reference returned after creating an offer request."""

    id: str = Field(min_length=1, max_length=256)


class DuffelOfferRequestResponse(DuffelResponseModel):
    """Duffel create-offer-request response envelope."""

    data: DuffelOfferRequestReference


class DuffelPlace(DuffelResponseModel):
    """Minimal airport or city data required by the mapper."""

    iata_code: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )

    @field_validator("iata_code", mode="before")
    @classmethod
    def normalize_iata_code(cls, value: object) -> object:
        """Normalize Duffel location codes."""

        if isinstance(value, str):
            return value.strip().upper()
        return value


class DuffelCarrier(DuffelResponseModel):
    """Minimal marketing or operating carrier data."""

    name: str = Field(min_length=1, max_length=120)
    iata_code: str = Field(
        min_length=2,
        max_length=3,
        pattern=r"^[A-Z0-9]{2,3}$",
    )

    @field_validator("iata_code", mode="before")
    @classmethod
    def normalize_carrier_code(cls, value: object) -> object:
        """Normalize Duffel carrier codes."""

        if isinstance(value, str):
            return value.strip().upper()

        return value


class DuffelSegmentResponse(DuffelResponseModel):
    """Minimal segment fields required for normalized flight output."""

    origin: DuffelPlace
    destination: DuffelPlace
    departing_at: datetime
    arriving_at: datetime
    duration: timedelta = Field(gt=timedelta(0))

    marketing_carrier: DuffelCarrier
    marketing_carrier_flight_number: str = Field(
        min_length=1,
        max_length=8,
    )
    operating_carrier: DuffelCarrier
    operating_carrier_flight_number: str = Field(
        min_length=1,
        max_length=8,
    )

    @field_validator(
        "marketing_carrier_flight_number",
        "operating_carrier_flight_number",
        mode="before",
    )
    @classmethod
    def normalize_flight_numbers(cls, value: object) -> object:
        """Normalize Duffel flight numbers before output mapping."""

        if isinstance(value, str):
            return value.strip().upper()
        return value


class DuffelSliceResponse(DuffelResponseModel):
    """One Duffel itinerary slice containing flight segments."""

    duration: timedelta = Field(gt=timedelta(0))
    segments: list[DuffelSegmentResponse] = Field(min_length=1)


class DuffelOfferPassenger(DuffelResponseModel):
    """Minimal passenger reference included in an offer."""

    id: str = Field(min_length=1, max_length=256)


class DuffelOfferResponse(DuffelResponseModel):
    """Minimal Duffel offer required for normalized search output."""

    id: str = Field(min_length=1, max_length=256)
    total_amount: Decimal = Field(ge=0)
    total_currency: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    expires_at: datetime
    slices: list[DuffelSliceResponse] = Field(
        min_length=1,
        max_length=2,
    )
    passengers: list[DuffelOfferPassenger] = Field(min_length=1)

    @field_validator("total_currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        """Normalize Duffel's billing currency."""

        if isinstance(value, str):
            return value.strip().upper()

        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expiry_timezone(cls, value: datetime) -> datetime:
        """Require an absolute Duffel offer expiry."""

        if value.utcoffset() is None:
            raise ValueError("Duffel offer expires_at must include a timezone")

        return value


class DuffelOffersListResponse(DuffelResponseModel):
    """Bounded Duffel list-offers response envelope."""

    data: list[DuffelOfferResponse] = Field(
        default_factory=list,
        max_length=10,
    )
