"""Versioned business state for conversational planning, independent of graphs."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.trip_requirements import TripRequirements


class PlanningState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    requirements: TripRequirements = Field(default_factory=TripRequirements)
    phase: Literal["idle", "collecting", "ready", "generated"] = "idle"
    language: Literal["en", "ur-Latn"] = "en"
    revision: int = Field(default=0, ge=0)
    requirements_message_id: UUID | None = None
    pending_fields: tuple[str, ...] = Field(default=(), max_length=20)
    # Compact, validated result retained for revisions; not raw tool transcripts.
    itinerary: dict[str, object] | None = None
    research: dict[str, object] | None = None
