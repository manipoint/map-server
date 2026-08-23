"""Flight-search MCP tool registration."""

from datetime import date
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from app.domain.flights import FlightCabinClass
from app.providers.flights.client import FlightProvider
from app.providers.flights.schemas import (
    ChildAge,
    FlightSearchInput,
    FlightSearchResult,
    InfantAge,
)


def register_flight_tools(
    server: FastMCP,
    *,
    flight_provider: FlightProvider,
) -> None:
    """Register normalized flight-search tools on an MCP server."""

    @server.tool(
        name="search_flights",
        description=(
            "Search available flight offers for a route and passenger group. "
            "Provide each child's age and each infant's age in the appropriate "
            "seat or lap age list. "
            "The returned total price covers all requested travelers. "
            "This tool searches flights only and does not make bookings."
        ),
    )
    async def search_flights(
        origin: Annotated[
            str,
            Field(min_length=3, max_length=3),
        ],
        destination: Annotated[
            str,
            Field(min_length=3, max_length=3),
        ],
        departure_date: date,
        return_date: date | None = None,
        adults: Annotated[int, Field(ge=1)] = 1,
        children_ages: list[ChildAge] | None = None,
        infants_with_seat_ages: list[InfantAge] | None = None,
        infants_on_lap_ages: list[InfantAge] | None = None,
        cabin_class: FlightCabinClass = FlightCabinClass.ECONOMY,
        nonstop_only: bool = False,
        currency: Annotated[
            str,
            Field(min_length=3, max_length=3),
        ] = "USD",
        max_results: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> FlightSearchResult:
        """Validate and execute one flight search."""
        request = FlightSearchInput(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            adults=adults,
            children_ages=children_ages or [],
            infants_with_seat_ages=infants_with_seat_ages or [],
            infants_on_lap_ages=infants_on_lap_ages or [],
            cabin_class=cabin_class,
            nonstop_only=nonstop_only,
            currency=currency,
            max_results=max_results,
        )
        return await flight_provider.search_flights(request=request)
