"""Place-search MCP response schemas."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

LocationCandidateText = Annotated[
    str,
    Field(min_length=2, max_length=200),
]


class PlaceSearchGuidance(BaseModel):
    """User guidance when place discovery cannot execute yet."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    status: Literal[
        "location_not_found",
        "location_ambiguous",
    ]
    message: str = Field(min_length=1, max_length=500)
    candidates: list[LocationCandidateText] = Field(
        default_factory=list,
        max_length=5,
    )

    @model_validator(mode="after")
    def validate_guidance(self) -> Self:
        """Keep clarification candidates consistent with status."""

        if self.status == "location_ambiguous" and not self.candidates:
            raise ValueError("location_ambiguous requires candidates")

        if self.status != "location_ambiguous" and self.candidates:
            raise ValueError("only location_ambiguous may contain candidates")

        return self
