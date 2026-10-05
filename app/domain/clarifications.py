"""Provider-independent structured clarification contracts."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.airports.schemas import AirportOption, AirportResolution


class AirportInputRequest(BaseModel):
    """One unresolved route endpoint requiring user input."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    field: Literal["origin_airport", "destination_airport"]
    query: str = Field(min_length=2, max_length=120)
    status: Literal["selection_required", "not_found"]
    question: str = Field(min_length=1, max_length=300)
    options: list[AirportOption] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        """Keep selection and no-match payloads structurally distinct."""

        if self.status == "selection_required" and len(self.options) < 2:
            raise ValueError("selection_required needs at least two options")
        if self.status == "not_found" and self.options:
            raise ValueError("not_found cannot contain options")
        return self


class TravelClarification(BaseModel):
    """Structured user input requested by one assistant response."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["airport_selection"] = "airport_selection"
    requests: list[AirportInputRequest] = Field(min_length=1, max_length=2)


def build_airport_input_request(
    *,
    field: str,
    resolution: AirportResolution,
) -> AirportInputRequest | None:
    """Map one unresolved airport result to a stable client request."""

    if resolution.status == "resolved":
        return None
    public_field = "origin_airport" if field == "origin" else "destination_airport"
    if resolution.status == "selection_required":
        question = f"Select an airport for {resolution.query}."
    else:
        question = (
            f"No airport matched {resolution.query}. "
            "Provide the city with country or region."
        )
    return AirportInputRequest(
        field=public_field,
        query=resolution.query,
        status=resolution.status,
        question=question,
        options=resolution.options,
    )
