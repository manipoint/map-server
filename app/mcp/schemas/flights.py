"""Flight-search MCP schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.providers.flights.schemas import FlightSearchInput


class FlightSearchGuidance(BaseModel):
    """Safe user guidance when a flight search cannot execute."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    status: Literal["invalid_dates"]
    message: str = Field(
        min_length=1,
        max_length=500,
    )


__all__ = [
    "FlightSearchGuidance",
    "FlightSearchInput",
]
