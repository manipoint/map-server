"""Public schemas for canonical location resolution."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.trips import CanonicalLocation

LocationQuery = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=120,
    ),
]


class LocationResolutionResponse(BaseModel):
    """Bounded provider-qualified options for one user query."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=120)
    options: list[CanonicalLocation] = Field(default_factory=list, max_length=5)
