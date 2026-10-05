"""Itinerary lifecycle and item classifications."""

from enum import StrEnum
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domain.media import AssistantMedia

MAX_ITINERARY_DAYS = 30
MAX_ITINERARY_ITEMS = 200


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


class ItineraryActivity(BaseModel):
    """Shared activity content for generation, persistence and presentation."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    day_number: int = Field(ge=1)
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
    start_time_zone: str | None = Field(default=None, max_length=64)
    end_time_zone: str | None = Field(default=None, max_length=64)
    image: AssistantMedia | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        """Require an end time after its corresponding start time."""

        for field, zone_name in (
            ("starts_at", self.start_time_zone),
            ("ends_at", self.end_time_zone),
        ):
            if zone_name is not None:
                try:
                    zone = ZoneInfo(zone_name)
                except (ZoneInfoNotFoundError, ValueError) as error:
                    raise ValueError("Invalid schedule time zone") from error
                value = getattr(self, field)
                if value is not None:
                    setattr(self, field, value.astimezone(zone))

        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be after starts_at")
        return self


class ItineraryItemDraft(ItineraryActivity):
    """One validated activity with its deterministic day position."""

    position: int = Field(ge=1)
