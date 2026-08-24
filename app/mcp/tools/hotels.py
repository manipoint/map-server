"""Hotel-search MCP tool registration."""

from datetime import date
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from app.common.exceptions import (
    AmbiguousLocationError,
    InvalidTravelDateError,
    LocationNotFoundError,
)
from app.mcp.schemas.hotels import HotelSearchGuidance
from app.providers.hotels.schemas import (
    HotelChildAge,
    HotelSearchInput,
    HotelSearchResult,
)
from app.services.hotel_search_service import HotelSearchService


def register_hotel_tools(
    server: FastMCP,
    *,
    hotel_search_service: HotelSearchService,
) -> None:
    """Register normalized hotel-search tools on an MCP server."""

    @server.tool(
        name="search_hotels",
        description=(
            "Search current hotel availability for a destination, dates, "
            "rooms, adults, and exact child ages. Include the country or "
            "region when known to avoid ambiguous destinations. A search "
            "price is not a final booking quote. This tool does not book."
        ),
    )
    async def search_hotels(
        destination: Annotated[str, Field(min_length=2, max_length=120)],
        check_in_date: date,
        check_out_date: date,
        adults: Annotated[int, Field(ge=1)] = 1,
        children_ages: list[HotelChildAge] | None = None,
        rooms: Annotated[int, Field(ge=1)] = 1,
        free_cancellation_only: bool = False,
        max_results: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> HotelSearchResult | HotelSearchGuidance:
        """Validate, resolve, and execute one hotel search."""
        request = HotelSearchInput(
            destination=destination,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            adults=adults,
            children_ages=children_ages or [],
            rooms=rooms,
            free_cancellation_only=free_cancellation_only,
            max_results=max_results,
        )
        try:
            return await hotel_search_service.search_hotels(request=request)
        except AmbiguousLocationError as error:
            return HotelSearchGuidance(
                status="location_ambiguous",
                message=("Multiple destinations matched. Please select one location."),
                candidates=list(error.candidates),
            )
        except LocationNotFoundError:
            return HotelSearchGuidance(
                status="location_not_found",
                message="No matching destination was found.",
            )
        except InvalidTravelDateError as error:
            return HotelSearchGuidance(status="invalid_dates", message=str(error))
