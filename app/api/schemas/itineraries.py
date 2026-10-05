"""Public REST schemas for itinerary resources."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.itineraries import (
    ItineraryItemDraft,
    ItineraryStatus,
)


class ItineraryCreateRequest(BaseModel):
    """A complete ordered timeline used to create one draft version."""

    model_config = ConfigDict(extra="forbid")
    items: list[ItineraryItemDraft] = Field(min_length=1, max_length=200)


class ItineraryItemResponse(ItineraryItemDraft):
    """Public representation of one ordered itinerary item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ItineraryResponse(BaseModel):
    """Public representation of one versioned itinerary and its timeline."""

    id: UUID
    trip_id: UUID
    version: int
    status: ItineraryStatus
    created_at: datetime
    updated_at: datetime
    items: list[ItineraryItemResponse]
