"""Provider-independent hotel-search schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from app.domain.hotels import HotelSearchStatus
from app.domain.value_objects import CountryCode, CurrencyCode
from app.providers.locations.schemas import ResolvedLocation

HotelChildAge = Annotated[int, Field(ge=0, le=17)]
AmenityText = Annotated[
    str,
    Field(min_length=1, max_length=120),
]


class HotelSearchInput(BaseModel):
    """Validated user-facing hotel-search request."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    destination: str = Field(
        min_length=2,
        max_length=120,
    )
    check_in_date: date
    check_out_date: date
    adults: int = Field(default=1, ge=1)
    children_ages: list[HotelChildAge] = Field(
        default_factory=list,
    )
    rooms: int = Field(default=1, ge=1)
    free_cancellation_only: bool = False
    max_results: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def validate_stay(self) -> Self:
        """Validate dates and guest-to-room relationships."""

        if self.check_out_date <= self.check_in_date:
            raise ValueError("check_out_date must be after check_in_date")

        if self.nights > 99:
            raise ValueError("hotel stay cannot exceed 99 nights")

        if self.adults < self.rooms:
            raise ValueError("each room requires at least one adult")

        return self

    @property
    def nights(self) -> int:
        """Return the number of requested hotel nights."""
        return (self.check_out_date - self.check_in_date).days

    @property
    def total_guests(self) -> int:
        """Return adults and children included in the search."""

        return self.adults + len(self.children_ages)


class ResolvedHotelSearch(BaseModel):
    """A validated hotel request with provider-ready coordinates."""

    model_config = ConfigDict(extra="forbid")

    request: HotelSearchInput
    location: ResolvedLocation
    radius_km: int = Field(ge=1, le=100)


class HotelProperty(BaseModel):
    """Compact provider-independent hotel information."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    accommodation_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)

    rating: int | None = Field(default=None, ge=1, le=5)
    review_score: Decimal | None = Field(
        default=None,
        ge=1,
        le=10,
    )
    review_count: int | None = Field(default=None, ge=0)

    address: str | None = Field(default=None, max_length=500)
    city_name: str = Field(min_length=1, max_length=120)
    country_code: CountryCode

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    amenities: list[AmenityText] = Field(
        default_factory=list,
        max_length=20,
    )
    photo_urls: list[HttpUrl] = Field(
        default_factory=list,
        max_length=3,
    )


class HotelSearchOption(BaseModel):
    """One currently available hotel with its cheapest search price."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    search_result_id: str = Field(min_length=1, max_length=256)
    hotel: HotelProperty

    check_in_date: date
    check_out_date: date
    rooms: int = Field(ge=1)
    guest_count: int = Field(ge=1)

    cheapest_total_price: Decimal = Field(ge=0)
    currency: CurrencyCode

    expires_at: datetime
    price_is_final: Literal[False] = False

    @model_validator(mode="after")
    def validate_option(self) -> Self:
        """Validate stay dates and absolute search-result expiry."""

        if self.check_out_date <= self.check_in_date:
            raise ValueError("hotel option checkout must be after check-in")

        if self.guest_count < self.rooms:
            raise ValueError("hotel option requires at least one guest per room")

        if self.expires_at.utcoffset() is None:
            raise ValueError("hotel option expires_at must include a timezone")

        return self


class HotelSearchResult(BaseModel):
    """Normalized bounded hotel-search result."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    status: HotelSearchStatus
    searched_at: datetime
    location: ResolvedLocation
    options: list[HotelSearchOption] = Field(
        default_factory=list,
        max_length=10,
    )
    message: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        """Keep status, timestamps, and options consistent."""

        if self.searched_at.utcoffset() is None:
            raise ValueError("searched_at must include a timezone")

        if self.status is HotelSearchStatus.HOTELS_AVAILABLE and not self.options:
            raise ValueError("hotels_available requires at least one option")

        if self.status is HotelSearchStatus.NO_HOTELS and self.options:
            raise ValueError("no_hotels cannot contain options")

        return self
