"""Flight-search MCP tool registration."""

from datetime import date
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from app.common.exceptions import InvalidTravelDateError
from app.domain.flights import FlightCabinClass
from app.mcp.schemas.flights import (
    FlightSearchGuidance,
    FlightSearchPreparationGuidance,
)
from app.providers.flights.schemas import (
    ChildAge,
    FlightSearchResult,
    InfantAge,
)
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationInput,
    FlightSearchPreparationService,
)


def register_flight_tools(
    server: FastMCP,
    *,
    flight_search_service: FlightSearchPreparationService,
) -> None:
    """Register normalized flight-search tools on an MCP server."""

    @server.tool(
        name="search_flights",
        description=(
            "Search available flight offers using airport codes, airport names, "
            "or city names for a route and passenger group. Ambiguous locations "
            "return choices instead of being guessed. "
            "Provide each child's age and each infant's age in the appropriate "
            "seat or lap age list. The returned total price covers all "
            "requested travelers. Groups above the supported online limit "
            "receive group-booking guidance without a provider search. "
            "This tool searches flights only and does not make bookings."
        ),
    )
    async def search_flights(
        origin: Annotated[
            str,
            Field(min_length=2, max_length=120),
        ],
        destination: Annotated[
            str,
            Field(min_length=2, max_length=120),
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
        max_results: Annotated[
            int,
            Field(ge=1, le=10),
        ] = 5,
    ) -> FlightSearchResult | FlightSearchGuidance | FlightSearchPreparationGuidance:
        """Resolve route locations and execute one safe flight search."""

        request = FlightSearchPreparationInput(
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

        try:
            return await flight_search_service.prepare_and_search(request=request)
        except InvalidTravelDateError as error:
            return FlightSearchGuidance(
                status="invalid_dates",
                message=str(error),
            )
