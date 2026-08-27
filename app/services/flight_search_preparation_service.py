"""Deterministic airport resolution before flight search."""

import asyncio
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.flights import FlightCabinClass
from app.domain.value_objects import CurrencyCode
from app.providers.airports.schemas import AirportResolution, AirportSearchInput
from app.providers.flights.schemas import (
    ChildAge,
    FlightSearchInput,
    FlightSearchResult,
    InfantAge,
)
from app.services.airport_resolution_service import AirportResolutionService
from app.services.flight_search_service import FlightSearchService


class FlightSearchPreparationInput(BaseModel):
    """Flight request whose locations may be names or IATA codes."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    origin: str = Field(min_length=2, max_length=120)
    destination: str = Field(min_length=2, max_length=120)
    departure_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=1)
    children_ages: list[ChildAge] = Field(default_factory=list)
    infants_with_seat_ages: list[InfantAge] = Field(default_factory=list)
    infants_on_lap_ages: list[InfantAge] = Field(default_factory=list)
    cabin_class: FlightCabinClass = FlightCabinClass.ECONOMY
    nonstop_only: bool = False
    currency: CurrencyCode = "USD"
    max_results: int = Field(default=5, ge=1, le=10)

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def normalize_direct_iata_codes(cls, value: object) -> object:
        """Uppercase direct ASCII IATA codes while preserving location names."""

        if isinstance(value, str):
            stripped_value = value.strip()
            if (
                len(stripped_value) == 3
                and stripped_value.isascii()
                and stripped_value.isalpha()
            ):
                return stripped_value.upper()
        return value

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        """Validate facts available before airport resolution."""

        if self.origin.casefold() == self.destination.casefold():
            raise ValueError("origin and destination must be different")
        if self.return_date is not None and self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        if len(self.infants_on_lap_ages) > self.adults:
            raise ValueError(
                "each lap infant must be accompanied by one adult; "
                "book additional infants with their own seat"
            )
        return self

    def to_flight_search(
        self,
        *,
        origin_iata_code: str,
        destination_iata_code: str,
    ) -> FlightSearchInput:
        """Build the provider request after both locations are resolved."""

        return FlightSearchInput(
            origin=origin_iata_code,
            destination=destination_iata_code,
            departure_date=self.departure_date,
            return_date=self.return_date,
            adults=self.adults,
            children_ages=self.children_ages,
            infants_with_seat_ages=self.infants_with_seat_ages,
            infants_on_lap_ages=self.infants_on_lap_ages,
            cabin_class=self.cabin_class,
            nonstop_only=self.nonstop_only,
            currency=self.currency,
            max_results=self.max_results,
        )

    @property
    def total_travelers(self) -> int:
        """Return every traveler represented by the preparation request."""

        return (
            self.adults
            + len(self.children_ages)
            + len(self.infants_with_seat_ages)
            + len(self.infants_on_lap_ages)
        )


class FlightSearchPreparationGuidance(BaseModel):
    """Airport choices or missing-location guidance before flight search."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["airport_resolution_required"] = "airport_resolution_required"
    origin: AirportResolution
    destination: AirportResolution
    message: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_unresolved_location(self) -> Self:
        """Prevent guidance when both route endpoints are already resolved."""

        if self.origin.status == "resolved" and self.destination.status == "resolved":
            raise ValueError("airport guidance requires an unresolved location")
        return self


FlightSearchPreparationResponse = FlightSearchResult | FlightSearchPreparationGuidance


class FlightSearchPreparationService:
    """Resolve both route endpoints before consuming flight-search quota."""

    def __init__(
        self,
        *,
        airport_resolution_service: AirportResolutionService,
        flight_search_service: FlightSearchService,
    ) -> None:
        self.airport_resolution_service = airport_resolution_service
        self.flight_search_service = flight_search_service

    async def prepare_and_search(
        self,
        *,
        request: FlightSearchPreparationInput,
    ) -> FlightSearchPreparationResponse:
        """Resolve locations concurrently and search only an unambiguous route."""

        policy_result = self.flight_search_service.evaluate_request_policy(
            request=request
        )
        if policy_result is not None:
            return policy_result

        origin, destination = await asyncio.gather(
            self.airport_resolution_service.resolve_airport(
                request=AirportSearchInput(query=request.origin)
            ),
            self.airport_resolution_service.resolve_airport(
                request=AirportSearchInput(query=request.destination)
            ),
        )

        if origin.status != "resolved" or destination.status != "resolved":
            return FlightSearchPreparationGuidance(
                origin=origin,
                destination=destination,
                message=self._build_resolution_message(
                    origin=origin,
                    destination=destination,
                ),
            )

        assert origin.iata_code is not None
        assert destination.iata_code is not None
        flight_request = request.to_flight_search(
            origin_iata_code=origin.iata_code,
            destination_iata_code=destination.iata_code,
        )
        return await self.flight_search_service.search_flights(request=flight_request)

    @staticmethod
    def _build_resolution_message(
        *,
        origin: AirportResolution,
        destination: AirportResolution,
    ) -> str:
        """Build concise guidance without exposing provider details."""

        unresolved_labels: list[str] = []
        if origin.status != "resolved":
            unresolved_labels.append("origin")
        if destination.status != "resolved":
            unresolved_labels.append("destination")

        labels = " and ".join(unresolved_labels)
        return (
            f"Airport resolution is required for the {labels}. "
            "Ask the user to select from returned choices or provide a city "
            "with country/region when no match was found."
        )
