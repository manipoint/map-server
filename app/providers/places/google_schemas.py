"""Validated Google Places Text Search transport schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from pydantic.alias_generators import to_camel


class GooglePlacesRequestModel(BaseModel):
    """Base model for requests sent to Google Places."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class GooglePlacesResponseModel(BaseModel):
    """Base model for responses received from Google Places."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class GooglePlaceTextSearchRequest(GooglePlacesRequestModel):
    """One cost-bounded Google Places Text Search request."""

    text_query: str = Field(min_length=2, max_length=400)
    page_size: int = Field(default=5, ge=1, le=10)
    language_code: str = Field(default="en", min_length=2, max_length=10)
    region_code: str | None = Field(default=None, min_length=2, max_length=2)
    included_type: str | None = Field(default=None, min_length=1, max_length=100)
    strict_type_filtering: bool = False
    rank_preference: Literal["RELEVANCE", "DISTANCE"] | None = None


class GoogleLocalizedText(GooglePlacesResponseModel):
    """Localized human-readable text returned by Google."""

    text: str = Field(min_length=1, max_length=500)
    language_code: str | None = Field(default=None, max_length=20)


class GoogleLatLng(GooglePlacesResponseModel):
    """Geographic coordinates for one place."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class GoogleAddressComponent(GooglePlacesResponseModel):
    """One typed component used to derive an ISO country code."""

    long_text: str = Field(min_length=1, max_length=200)
    short_text: str | None = Field(default=None, min_length=1, max_length=50)
    types: list[str] = Field(min_length=1, max_length=10)


class GooglePlaceResponse(GooglePlacesResponseModel):
    """Relevant canonical fields for one Google place."""

    id: str = Field(min_length=1, max_length=300)
    display_name: GoogleLocalizedText
    formatted_address: str | None = Field(default=None, max_length=500)
    location: GoogleLatLng | None = None
    types: list[str] = Field(default_factory=list, max_length=30)
    primary_type: str | None = Field(default=None, max_length=100)
    website_uri: HttpUrl | None = None
    google_maps_uri: HttpUrl | None = None
    address_components: list[GoogleAddressComponent] = Field(
        default_factory=list,
        max_length=30,
    )


class GooglePlaceTextSearchResponse(GooglePlacesResponseModel):
    """Relevant fields returned by Google Places Text Search."""

    places: list[GooglePlaceResponse] = Field(
        default_factory=list,
        max_length=20,
    )
    next_page_token: str | None = Field(default=None, max_length=2000)
    search_uri: HttpUrl | None = None
