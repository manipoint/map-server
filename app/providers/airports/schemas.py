"""Provider-independent airport and city-code lookup schemas."""

from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.domain.value_objects import CountryCode, IataCode


class AirportSearchInput(BaseModel):
    """Validated airport or city lookup request."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    query: str = Field(
        min_length=2,
        max_length=120,
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=5,
    )


class AirportOption(BaseModel):
    """One normalized airport or metropolitan city-code option."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    provider_location_id: str = Field(
        min_length=1,
        max_length=256,
    )
    iata_code: IataCode
    location_type: Literal["airport", "city"]

    name: str = Field(
        min_length=1,
        max_length=200,
    )
    city_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )

    country_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    country_code: CountryCode

    @property
    def display_name(self) -> str:
        """Return a compact label suitable for user clarification."""

        parts = [self.name]
        if (
            self.city_name is not None
            and self.city_name.casefold() != self.name.casefold()
        ):
            parts.append(self.city_name)

        parts.append(self.country_name or self.country_code)
        parts.append(self.iata_code)

        return ", ".join(parts)


class AirportSearchResult(BaseModel):
    """Bounded airport lookup results for one user query."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    query: str = Field(
        min_length=2,
        max_length=120,
    )
    options: list[AirportOption] = Field(
        default_factory=list,
        max_length=5,
    )

    @model_validator(mode="after")
    def deduplicate_iata_codes(self) -> Self:
        """Keep only the first option for each normalized IATA code."""

        unique_options: list[AirportOption] = []
        seen_codes: set[str] = set()

        for option in self.options:
            if option.iata_code in seen_codes:
                continue
            unique_options.append(option)
            seen_codes.add(option.iata_code)
        self.options = unique_options
        return self


class AirportResolution(BaseModel):
    """Resolved IATA code or bounded user-selection options."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    status: Literal[
        "resolved",
        "selection_required",
        "not_found",
    ]
    query: str = Field(
        min_length=2,
        max_length=120,
    )
    iata_code: IataCode | None = None
    options: list[AirportOption] = Field(
        default_factory=list,
        max_length=5,
    )

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        """Keep resolution fields consistent with their status."""

        if self.status == "resolved":
            if self.iata_code is None:
                raise ValueError("resolved airport requires iata_code")

            if self.options:
                raise ValueError("resolved airport cannot contain options")

        elif self.status == "selection_required":
            if self.iata_code is not None:
                raise ValueError("selection_required cannot contain iata_code")

            if len(self.options) < 2:
                raise ValueError("selection_required requires at least two options")

        elif self.iata_code is not None or self.options:
            raise ValueError("not_found cannot contain iata_code or options")

        return self
