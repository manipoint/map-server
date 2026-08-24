"""Graph-facing MCP client."""

from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from app.common.exceptions import ProviderUnavailableError
from app.mcp.schemas.hotels import HotelSearchGuidance
from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult
from app.providers.hotels.schemas import HotelSearchInput, HotelSearchResult
from app.providers.weather.schemas import CurrentWeather

HotelSearchResponse = HotelSearchResult | HotelSearchGuidance
HOTEL_SEARCH_RESPONSE_ADAPTER = TypeAdapter(HotelSearchResponse)


class McpToolServer(Protocol):
    """Minimum FastMCP interface required by graph-facing clients."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> Any:
        """Call a registered MCP tool."""


class TravelMcpClient:
    """Expose normalized travel data to LangGraph."""

    def __init__(self, mcp_server: McpToolServer) -> None:
        self.mcp_server = mcp_server

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Get validated normalized weather through the internal MCP server."""

        request = CurrentWeatherInput(city=city)
        try:
            result = await self.mcp_server.call_tool(
                "get_current_weather", {"city": request.city}
            )

        except Exception as error:
            raise ProviderUnavailableError(
                "Current-weather tool is unavailable"
            ) from error
        if result.is_error:
            raise ProviderUnavailableError("Current-weather tool failed")

        try:
            return CurrentWeather.model_validate(result.structured_content)
        except (AttributeError, TypeError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Current-weather tool returned an invalid response"
            ) from error

    async def search_flights(self, *, request: FlightSearchInput) -> FlightSearchResult:
        """Search flights through MCP and validate the normalized result."""

        arguments = request.model_dump(mode="json", exclude_none=True)
        try:
            result = await self.mcp_server.call_tool(
                "search_flights", arguments=arguments
            )
        except Exception as error:
            raise ProviderUnavailableError(
                "Flight-search tool is unavailable"
            ) from error
        if result.is_error:
            raise ProviderUnavailableError("Flight-search tool failed")
        try:
            return FlightSearchResult.model_validate(result.structured_content)
        except (AttributeError, TypeError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Flight-search tool returned an invalid response"
            ) from error

    async def search_hotels(self, *, request: HotelSearchInput) -> HotelSearchResponse:
        """Search hotels through MCP and validate normalized results or guidance."""
        arguments = request.model_dump(mode="json", exclude_none=True)
        try:
            result = await self.mcp_server.call_tool(
                "search_hotels",
                arguments=arguments,
            )
        except Exception as error:
            raise ProviderUnavailableError(
                "Hotel-search tool is unavailable"
            ) from error

        if result.is_error:
            raise ProviderUnavailableError("Hotel-search tool failed")
        try:
            payload = result.structured_content["result"]
            return HOTEL_SEARCH_RESPONSE_ADAPTER.validate_python(payload)

        except (AttributeError, KeyError, TypeError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Hotel-search tool returned an invalid response"
            ) from error
