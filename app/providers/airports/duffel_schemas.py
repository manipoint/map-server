"""Duffel Places Suggestions API response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import CountryCode, IataCode


class DuffelPlaceSuggestion(BaseModel):
    """One airport or metropolitan city returned by Duffel."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    id: str = Field(
        min_length=1,
        max_length=256,
    )
    iata_code: IataCode
    type: Literal["airport", "city"]

    name: str = Field(
        min_length=1,
        max_length=200,
    )
    city_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    iata_country_code: CountryCode


class DuffelPlaceSuggestionsResponse(BaseModel):
    """Validated response from Duffel place suggestions."""

    model_config = ConfigDict(
        extra="ignore",
    )

    data: list[DuffelPlaceSuggestion] = Field(
        default_factory=list,
    )
