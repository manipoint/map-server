"""Bounded model outputs used by the planning state machine."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.graph.schemas.itineraries import GeneratedItineraryItem

MAX_PLANNING_DAYS = 30
MAX_STRUCTURED_RESPONSE_CHARS = 100_000


class RequirementExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    intent: Literal["chat", "plan", "revise", "new_trip"]
    language: Literal["en", "ur-Latn"] = "en"
    updates: dict[str, JsonValue] = Field(default_factory=dict, max_length=24)
    reply: str = Field(default="", max_length=2000)


class ResearchedItineraryItem(GeneratedItineraryItem):
    evidence_id: str | None = Field(default=None, max_length=64)


class ResearchedItinerary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=500)
    items: list[ResearchedItineraryItem] = Field(min_length=1, max_length=200)
