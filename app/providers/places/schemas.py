"""Provider-independent place-discovery schemas."""

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.domain.places import PlaceSearchStatus
from app.providers.locations.schemas import ResolvedLocation

InterestText = Annotated[str, Field(min_length=1, max_length=80)]
CategoryText = Annotated[str, Field(min_length=1, max_length=80)]


class PlaceSearchInput(BaseModel):
    """Validated destination and preferences for place discovery."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    destination: str = Field(min_length=2, max_length=120)
    interests: list[InterestText] = Field(default_factory=list, max_length=10)
    family_friendly: bool | None = None
    max_results: int = Field(default=5, ge=1, le=5)

    @model_validator(mode="after")
    def deduplicate_interests(self) -> Self:
        """Remove repeated interests without changing their original order."""

        unique_interests: list[str] = []
        seen: set[str] = set()
        for interest in self.interests:
            key = interest.casefold()
            if key not in seen:
                seen.add(key)
                unique_interests.append(interest)
        self.interests = unique_interests
        return self


class PlaceOption(BaseModel):
    """One evidence-backed attraction or activity."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    provider_place_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
    )
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    categories: list[CategoryText] = Field(default_factory=list, max_length=10)
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    website_url: HttpUrl | None = None
    source_urls: list[HttpUrl] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_place(self) -> Self:
        """Validate coordinates and deduplicate evidence URLs."""

        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")

        unique_sources: list[HttpUrl] = []
        seen: set[str] = set()

        for source_url in self.source_urls:
            key = str(source_url)
            if key not in seen:
                seen.add(key)
                unique_sources.append(source_url)

        self.source_urls = unique_sources
        return self


class PlaceSearchResult(BaseModel):
    """Normalized bounded result from a place-discovery provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: PlaceSearchStatus
    searched_at: datetime
    location: ResolvedLocation
    places: list[PlaceOption] = Field(default_factory=list, max_length=10)
    message: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        """Keep timestamps, status, and returned places consistent."""

        if self.searched_at.utcoffset() is None:
            raise ValueError("searched_at must include a timezone")
        if self.status is PlaceSearchStatus.PLACES_AVAILABLE and not self.places:
            raise ValueError("places_available requires at least one place")
        if self.status is PlaceSearchStatus.NO_PLACES and self.places:
            raise ValueError("no_places cannot contain places")
        return self


class ResolvedPlaceSearch(BaseModel):
    """A validated place request with one deterministic destination."""

    model_config = ConfigDict(extra="forbid")

    request: PlaceSearchInput
    location: ResolvedLocation
