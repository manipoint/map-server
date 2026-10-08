"""Versioned business state for conversational planning, independent of graphs."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.trip_requirements import TripRequirements


class PendingTravelSelection(BaseModel):
    """A provider search result that the user must choose or revise."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["flight", "hotel", "flight_dates"]
    option_ids: tuple[Annotated[str, Field(max_length=300)], ...] = Field(
        default=(), max_length=10
    )
    reason: Literal["choose", "no_results"]


class PlanningState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    requirements: TripRequirements = Field(default_factory=TripRequirements)
    transport_inferred: bool = False
    phase: Literal["idle", "collecting", "ready", "generated"] = "idle"
    language: Literal["en", "ur-Latn"] = "en"
    revision: int = Field(default=0, ge=0)
    last_turn_number: int = Field(default=0, ge=0)
    interests_overridden: bool = False
    requirements_message_id: UUID | None = None
    context_start_message_id: UUID | None = None
    pending_fields: tuple[str, ...] = Field(default=(), max_length=20)
    # Compact, validated result retained for revisions; not raw tool transcripts.
    itinerary: dict[str, object] | None = None
    research: dict[str, object] | None = None
    research_key: str | None = None
    pending_search: dict[str, object] | None = None
    pending_travel_selection: PendingTravelSelection | None = None
    selected_flight_id: str | None = Field(default=None, max_length=300)
    selected_hotel_id: str | None = Field(default=None, max_length=300)
    nearby_flight_dates_checked: bool = False
