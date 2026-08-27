"""Airport-resolution MCP tool registration."""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from app.providers.airports.schemas import (
    AirportResolution,
    AirportSearchInput,
)
from app.services.airport_resolution_service import AirportResolutionService


def register_airport_tools(
    server: FastMCP,
    *,
    airport_resolution_service: AirportResolutionService,
) -> None:
    """Register the normalized airport-resolution tool."""

    @server.tool(
        name="resolve_airport",
        description=(
            "Resolve an airport or city name to an IATA code before a flight "
            "search. Direct three-letter IATA codes are accepted without an "
            "external lookup. Ambiguous cities return bounded choices; never "
            "guess between them."
        ),
    )
    async def resolve_airport(
        query: Annotated[str, Field(min_length=2, max_length=120)],
        max_results: Annotated[int, Field(ge=1, le=5)] = 5,
    ) -> AirportResolution:
        """Validate and resolve one airport or metropolitan query."""

        request = AirportSearchInput(
            query=query,
            max_results=max_results,
        )
        return await airport_resolution_service.resolve_airport(request=request)
