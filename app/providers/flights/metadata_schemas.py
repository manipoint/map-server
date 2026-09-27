"""Provider-independent airport and airline metadata."""

from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.value_objects import IataCode


class AirportMetadata(BaseModel):
    """Airport timezone required for interpreting local schedules."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )
    iata_code: IataCode
    time_zone: str = Field(min_length=1, max_length=64)

    @field_validator("time_zone")
    @classmethod
    def validate_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Expected a recognized IANA timezone") from None
        return value


class AirlineMetadata(BaseModel):
    """Display name associated with a carrier code."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    carrier_code: str = Field(
        min_length=2,
        max_length=3,
        pattern=r"^[A-Z0-9]{2,3}$",
    )
    name: str = Field(min_length=1, max_length=120)

    @field_validator("carrier_code", mode="before")
    @classmethod
    def normalize_carrier_code(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()

        return value


class FlightMetadata(BaseModel):
    """Resolved metadata indexed by canonical airport/carrier codes."""

    model_config = ConfigDict(extra="forbid")

    airports: dict[str, AirportMetadata] = Field(
        default_factory=dict,
    )
    airlines: dict[str, AirlineMetadata] = Field(
        default_factory=dict,
    )

    @model_validator(mode="after")
    def validate_lookup_keys(self) -> Self:
        for code, airport in self.airports.items():
            if code != airport.iata_code:
                raise ValueError("Airport lookup key must match its IATA code")

        for code, airline in self.airlines.items():
            if code != airline.carrier_code:
                raise ValueError("Airline lookup key must match its carrier code")

        return self
