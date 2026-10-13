"""Recognized country inputs and validated pending city selections."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.city_suggestions import (
    MAX_CITY_SUGGESTIONS,
    CitySuggestion,
)
from app.domain.value_objects import CountryCode


class CountryMatch(BaseModel):
    """A recognized country or spelling correction requiring confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    country_code: CountryCode
    country_name: str = Field(min_length=2, max_length=120)
    query: str = Field(min_length=2, max_length=120)
    requires_confirmation: bool = False


class CountryRecognition(BaseModel):
    """Country proposals; typo and ambiguous results require user confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal[
        "exact",
        "confirmation_required",
        "ambiguous",
        "not_recognized",
        "invalid_input",
    ]
    matches: tuple[CountryMatch, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def validate_matches(self) -> Self:
        if self.status in {"invalid_input", "not_recognized"}:
            if self.matches:
                raise ValueError("Unrecognized input cannot contain matches")
            return self
        if self.status == "ambiguous":
            if len(self.matches) < 2:
                raise ValueError("Ambiguous recognition requires multiple matches")
        elif len(self.matches) != 1:
            raise ValueError("Recognition requires exactly one match")

        codes = [match.country_code for match in self.matches]
        if len(codes) != len(set(codes)):
            raise ValueError("Country matches must have unique codes")
        needs_confirmation = self.status != "exact"
        if any(
            match.requires_confirmation != needs_confirmation for match in self.matches
        ):
            raise ValueError("Confirmation flags must match recognition status")
        return self


class PendingDestinationSelection(BaseModel):
    """Conversation-owned options; none represents a confirmed selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    country: CountryMatch
    options: tuple[CitySuggestion, ...] = Field(
        default=(), max_length=MAX_CITY_SUGGESTIONS
    )

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        destination_ids = [option.destination_id for option in self.options]
        if len(destination_ids) != len(set(destination_ids)):
            raise ValueError("Destination options must have unique IDs")
        if any(
            option.country_code != self.country.country_code for option in self.options
        ):
            raise ValueError("Destination options must belong to the requested country")

        return self
