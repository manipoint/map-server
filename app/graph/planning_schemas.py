"""Bounded model outputs used by the planning state machine."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from app.domain.itineraries import MAX_ITINERARY_DAYS, MAX_ITINERARY_ITEMS
from app.domain.trip_requirements import TripRequirements
from app.graph.schemas.itineraries import GeneratedItineraryItem
from app.services.standalone_search_service import StandaloneRequest

MAX_PLANNING_DAYS = MAX_ITINERARY_DAYS
MAX_STRUCTURED_RESPONSE_CHARS = 100_000


def _create_trip_requirements_patch_model() -> type[BaseModel]:
    """Reuse domain field types and constraints in a presence-aware patch."""
    patch_fields = {}
    for name, field in TripRequirements.model_fields.items():
        details = field.asdict()
        attributes = details["attributes"].copy()
        attributes.pop("default", None)
        attributes.pop("default_factory", None)
        annotation = details["annotation"] | None
        if details["metadata"]:
            annotation = Annotated[annotation, *details["metadata"]]
        patch_fields[name] = (annotation, Field(default=None, **attributes))

    return create_model(
        "TripRequirementsPatch",
        __config__=ConfigDict(extra="forbid", str_strip_whitespace=True),
        **patch_fields,
    )


TripRequirementsPatch = _create_trip_requirements_patch_model()
RequirementField = StrEnum(
    "RequirementField", {name.upper(): name for name in TripRequirements.model_fields}
)


class RequirementExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    intent: Literal["chat", "plan", "revise", "new_trip", "search"]
    language: Literal["en", "ur-Latn"] = "en"
    updates: TripRequirementsPatch
    changed_fields: list[RequirementField] = Field(
        max_length=len(TripRequirements.model_fields)
    )
    transport_inferred: bool = False
    reply: str = Field(default="", max_length=300)
    search: StandaloneRequest | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        if (self.intent == "search") != (self.search is not None):
            raise ValueError("Search intent requires exactly one typed search request")
        if len(set(self.changed_fields)) != len(self.changed_fields):
            raise ValueError("changed_fields must be unique")
        if any(
            name not in self.updates.model_fields_set for name in self.changed_fields
        ):
            raise ValueError("Every changed field must have a value in updates")
        return self

    def selected_updates(self) -> dict[str, object]:
        values = self.updates.model_dump(mode="json")
        return {name.value: values[name.value] for name in self.changed_fields}


class ResearchedItineraryItem(GeneratedItineraryItem):
    # These fields are attached from normalized evidence after model validation.
    # Keeping URL objects out of the generation schema also avoids unsupported
    # provider URI formats and gives the model no channel for arbitrary images.
    image: None = None
    start_time_zone: None = None
    end_time_zone: None = None
    evidence_id: str | None = Field(default=None, max_length=64)
    generic_activity: (
        Literal[
            "explore",
            "walk",
            "meal",
            "breakfast",
            "lunch",
            "dinner",
            "hike",
            "photography",
            "transfer",
            "rest",
            "free_time",
            "note",
        ]
        | None
    ) = None


class ResearchedItinerary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=500)
    items: list[ResearchedItineraryItem] = Field(
        min_length=1, max_length=MAX_ITINERARY_ITEMS
    )
