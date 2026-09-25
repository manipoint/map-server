"""Structured assistant content rendered by mobile clients."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)


class AssistantMedia(BaseModel):
    """A remotely hosted image displayed by the client."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    url: HttpUrl
    alt_text: str = Field(min_length=1, max_length=300)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_dimensions(self) -> Self:
        """Require width and height together when dimensions are provided."""

        if (self.width is None) != (self.height is None):
            raise ValueError("Image width and height must be provided together")

        return self


class AssistantMoney(BaseModel):
    """Currency-safe monetary value."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    amount: Decimal = Field(ge=0)
    currency: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    qualifier: Literal["total", "per_night", "from"] = "total"


class AssistantPlaceCard(BaseModel):
    """One suggested place displayed in a horizontal carousel."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    subtitle: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    image: AssistantMedia | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        """Require latitude and longitude together."""

        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Place latitude and longitude must be provided together")

        return self


class AssistantHotelCard(BaseModel):
    """One live hotel option displayed in a horizontal carousel."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    category: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    rating: int | None = Field(default=None, ge=1, le=5)
    review_score: Decimal | None = Field(
        default=None,
        ge=1,
        le=10,
    )
    price: AssistantMoney | None = None
    image: AssistantMedia | None = None
    expires_at: AwareDatetime | None = None


class AssistantPlaceCarousel(BaseModel):
    """A bounded collection of suggested places."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    type: Literal["place_carousel"] = "place_carousel"
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=120)
    items: list[AssistantPlaceCard] = Field(
        min_length=1,
        max_length=5,
    )


class AssistantHotelCarousel(BaseModel):
    """A bounded collection of live hotel options."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    type: Literal["hotel_carousel"] = "hotel_carousel"
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=120)
    items: list[AssistantHotelCard] = Field(
        min_length=1,
        max_length=5,
    )


class AssistantItinerarySummary(BaseModel):
    """Compact trip information required by an itinerary preview."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    duration_days: int = Field(ge=1)

    traveler_count: int | None = Field(
        default=None,
        ge=1,
        le=100,
    )

    cities: list[str] = Field(
        default_factory=list,
        max_length=20,
    )

    pace: Literal["relaxed", "balanced", "packed"] | None = None
    cover_image: AssistantMedia | None = None

    @model_validator(mode="after")
    def validate_trip_dates(self) -> Self:
        """Keep date range and duration consistent."""

        if self.end_date < self.start_date:
            raise ValueError("Itinerary end_date cannot precede start_date")

        expected_duration = (self.end_date - self.start_date).days + 1

        if self.duration_days != expected_duration:
            raise ValueError(
                "duration_days must match the inclusive itinerary date range"
            )

        return self


class AssistantItineraryDayPreview(BaseModel):
    """Compact day preview displayed inside an assistant message."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    day_number: int = Field(ge=1)
    date: date
    title: str = Field(min_length=1, max_length=200)
    subtitle: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
    )


class AssistantItineraryPreview(BaseModel):
    """Reference and preview for a persisted itinerary."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    type: Literal["itinerary_preview"] = "itinerary_preview"
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=120)
    itinerary_id: UUID
    summary: AssistantItinerarySummary

    # This is a chat preview, not the complete itinerary timeline.
    days: list[AssistantItineraryDayPreview] = Field(
        default_factory=list,
        max_length=7,
    )


AssistantContentSection = Annotated[
    AssistantPlaceCarousel | AssistantHotelCarousel | AssistantItineraryPreview,
    Field(discriminator="type"),
]


class AssistantRichContent(BaseModel):
    """Versioned structured content attached to an assistant response."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    type: Literal["rich_response"] = "rich_response"
    schema_version: Literal[1] = 1
    sections: list[AssistantContentSection] = Field(
        min_length=1,
        max_length=6,
    )
