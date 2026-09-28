"""Validated airport directory used by local lookup."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.airports.schemas import AirportOption


class AirportDirectory(BaseModel):
    """Canonical airport entries loaded from a trusted dataset."""

    model_config = ConfigDict(extra="forbid")

    airports: list[AirportOption] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_airport_entries(self) -> Self:
        seen_codes: set[str] = set()
        for airport in self.airports:
            if airport.location_type != "airport":
                raise ValueError(
                    "Airport directory entries must have airport location type"
                )

            if airport.iata_code in seen_codes:
                raise ValueError("Airport directory contains duplicate IATA codes")

            seen_codes.add(airport.iata_code)
        return self
