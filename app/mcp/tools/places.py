"""Place-search MCP tool registration."""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from app.common.exceptions import (
    AmbiguousLocationError,
    LocationNotFoundError,
)
from app.mcp.schemas.places import PlaceSearchGuidance
from app.providers.places.schemas import (
    InterestText,
    PlaceSearchInput,
    PlaceSearchResult,
)
from app.services.place_search_service import PlaceSearchService


def register_place_tools(
    server: FastMCP, *, place_search_service: PlaceSearchService
) -> None:
    """Register normalized place-discovery tools on an MCP server."""

    @server.tool(
        name="search_places",
        description=(
            "Find verified attractions and activities for one destination. "
            "Optionally provide interests such as museums, parks, history, "
            "food, or family activities. Include country or region when "
            "known to avoid ambiguous destinations. Returns at most five "
            "results. Discovery only; this tool does not book anything."
        ),
    )
    async def search_places(
        destination: Annotated[str, Field(min_length=2, max_length=120)],
        interests: Annotated[
            list[InterestText] | None,
            Field(max_length=10),
        ] = None,
        family_friendly: bool | None = None,
        max_results: Annotated[int, Field(ge=1, le=5)] = 5,
    ) -> PlaceSearchResult | PlaceSearchGuidance:
        """Validate, resolve, and execute one place search."""

        request = PlaceSearchInput(
            destination=destination,
            interests=interests or [],
            family_friendly=family_friendly,
            max_results=max_results,
        )
        try:
            return await place_search_service.search_places(request=request)
        except AmbiguousLocationError as error:
            return PlaceSearchGuidance(
                status="location_ambiguous",
                message=("Multiple destinations matched. Please select one location."),
                candidates=list(error.candidates),
            )
        except LocationNotFoundError:
            return PlaceSearchGuidance(
                status="location_not_found",
                message="No matching destination was found.",
            )
