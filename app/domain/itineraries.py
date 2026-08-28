"""Itinerary lifecycle and item classifications."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ItineraryStatus(StrEnum):
    """Lifecycle state of one itinerary version."""

    DRAFT = "draft"
    SAVED = "saved"
    SUPERSEDED = "superseded"


class ItineraryItemType(StrEnum):
    """Supported itinerary timeline item categories."""

    FLIGHT = "flight"
    HOTEL = "hotel"
    PLACE = "place"
    ACTIVITY = "activity"
    MEAL = "meal"
    TRANSFER = "transfer"
    NOTE = "note"


class ItineraryItemDraft(BaseModel):
    """Validated input for one itinerary timeline item."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    day_number: int = Field(ge=1)
    position: int = Field(ge=1)
    item_type: ItineraryItemType
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )
    location_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        """Require an end time after its corresponding start time."""

        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be after starts_at")
        return self
