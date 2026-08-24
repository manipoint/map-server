"""Validated Duffel Stays request and response schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from app.domain.value_objects import CountryCode, CurrencyCode


class DuffelStayGuest(BaseModel):
    """One adult or exact-age child in a hotel search."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["adult", "child"]
    age: int | None = Field(default=None, ge=0, le=17)

    @model_validator(mode="after")
    def validate_guest(self) -> Self:
        """Require age only for child guests."""

        if self.type == "adult" and self.age is not None:
            raise ValueError("adult Duffel stay guests must not include age")

        if self.type == "child" and self.age is None:
            raise ValueError("child Duffel stay guests require exact age")

        return self


class DuffelGeographicCoordinates(BaseModel):
    """Coordinates used as the center of a Duffel Stays search."""

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DuffelStayLocation(BaseModel):
    """Location and radius accepted by Duffel Stays."""

    model_config = ConfigDict(extra="forbid")

    radius: int = Field(ge=1, le=100)
    geographic_coordinates: DuffelGeographicCoordinates


class DuffelStaySearchData(BaseModel):
    """Duffel location-based accommodation search body."""

    model_config = ConfigDict(extra="forbid")

    location: DuffelStayLocation
    check_in_date: date
    check_out_date: date
    guests: list[DuffelStayGuest] = Field(min_length=1)
    rooms: int = Field(ge=1)
    free_cancellation_only: bool = False
    mobile: bool = True

    @model_validator(mode="after")
    def validate_search(self) -> Self:
        """Validate stay length and room-to-adult relationship."""

        if self.check_out_date <= self.check_in_date:
            raise ValueError("Duffel stay checkout must be after check-in")

        nights = (self.check_out_date - self.check_in_date).days
        if nights > 99:
            raise ValueError("Duffel stay cannot exceed 99 nights")

        adult_count = sum(guest.type == "adult" for guest in self.guests)
        if adult_count < self.rooms:
            raise ValueError("Duffel requires at least one adult per room")

        return self


class DuffelStaySearchPayload(BaseModel):
    """Top-level Duffel Stays search request envelope."""

    model_config = ConfigDict(extra="forbid")

    data: DuffelStaySearchData


class DuffelStayResponseModel(BaseModel):
    """Base model tolerant of new Duffel response fields."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )


class DuffelStayResponseCoordinates(DuffelStayResponseModel):
    """Coordinates returned for one accommodation."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DuffelStayAddressResponse(DuffelStayResponseModel):
    """Minimal accommodation address required by the app."""

    line_one: str | None = None
    line_two: str | None = None
    city_name: str = Field(min_length=1, max_length=120)
    region: str | None = None
    postal_code: str | None = None
    country_code: CountryCode


class DuffelStayAccommodationLocationResponse(DuffelStayResponseModel):
    """Address and coordinates for one accommodation."""

    address: DuffelStayAddressResponse
    geographic_coordinates: DuffelStayResponseCoordinates


class DuffelStayAmenityResponse(DuffelStayResponseModel):
    """One accommodation amenity."""

    type: str = Field(min_length=1)
    description: str | None = None


class DuffelStayPhotoResponse(DuffelStayResponseModel):
    """One accommodation photo."""

    url: HttpUrl


class DuffelStayAccommodationResponse(DuffelStayResponseModel):
    """Minimal Duffel accommodation content used by normalization."""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1)
    description: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)

    review_score: Decimal | None = Field(
        default=None,
        ge=1,
        le=10,
    )
    review_count: int | None = Field(default=None, ge=0)

    location: DuffelStayAccommodationLocationResponse
    amenities: list[DuffelStayAmenityResponse] | None = None
    photos: list[DuffelStayPhotoResponse] = Field(
        default_factory=list,
    )


class DuffelStaySearchResultResponse(DuffelStayResponseModel):
    """One accommodation option returned by a Duffel search."""

    id: str = Field(min_length=1, max_length=256)
    check_in_date: date
    check_out_date: date
    rooms: int = Field(ge=1)

    expires_at: datetime

    cheapest_rate_total_amount: Decimal = Field(ge=0)
    cheapest_rate_currency: CurrencyCode
    accommodation: DuffelStayAccommodationResponse

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        """Validate dates and absolute expiration timestamp."""
        if self.check_out_date <= self.check_in_date:
            raise ValueError("Duffel stay result checkout must be after check-in")

        if self.expires_at.utcoffset() is None:
            raise ValueError("Duffel stay result expires_at must include a timezone")

        return self


class DuffelStaySearchDataResponse(DuffelStayResponseModel):
    """Data returned from a Duffel accommodation search."""

    created_at: datetime
    results: list[DuffelStaySearchResultResponse]

    @model_validator(mode="after")
    def validate_data(self) -> Self:
        """Require an absolute provider search timestamp."""

        if self.created_at.utcoffset() is None:
            raise ValueError("Duffel stay search created_at must include a timezone")

        return self


class DuffelStaySearchResponse(DuffelStayResponseModel):
    """Top-level Duffel accommodation-search response."""

    data: DuffelStaySearchDataResponse
