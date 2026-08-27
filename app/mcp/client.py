"""Graph-facing MCP client."""

from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from app.common.exceptions import ProviderUnavailableError
from app.mcp.schemas.airports import AirportResolution
from app.mcp.schemas.currency import CurrencyConversionGuidance
from app.mcp.schemas.flights import (
    FlightSearchGuidance,
    FlightSearchPreparationGuidance,
    FlightSearchPreparationInput,
)
from app.mcp.schemas.hotels import HotelSearchGuidance
from app.mcp.schemas.places import PlaceSearchGuidance
from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.airports.schemas import AirportSearchInput
from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)
from app.providers.flights.schemas import FlightSearchResult
from app.providers.hotels.schemas import HotelSearchInput, HotelSearchResult
from app.providers.places.schemas import PlaceSearchInput, PlaceSearchResult
from app.providers.weather.schemas import CurrentWeather

HotelSearchResponse = HotelSearchResult | HotelSearchGuidance
PlaceSearchResponse = PlaceSearchResult | PlaceSearchGuidance
CurrencyConversionResponse = CurrencyConversionResult | CurrencyConversionGuidance
FlightSearchResponse = (
    FlightSearchResult | FlightSearchGuidance | FlightSearchPreparationGuidance
)

HOTEL_SEARCH_RESPONSE_ADAPTER = TypeAdapter(HotelSearchResponse)
PLACE_SEARCH_RESPONSE_ADAPTER = TypeAdapter(PlaceSearchResponse)
CURRENCY_CONVERSION_RESPONSE_ADAPTER = TypeAdapter(CurrencyConversionResponse)
FLIGHT_SEARCH_RESPONSE_ADAPTER = TypeAdapter(FlightSearchResponse)


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

    async def resolve_airport(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportResolution:
        """Resolve an airport query through the internal MCP server."""

        arguments = request.model_dump(mode="json")
        try:
            result = await self.mcp_server.call_tool(
                "resolve_airport",
                arguments=arguments,
            )
        except Exception as error:
            raise ProviderUnavailableError(
                "Airport-resolution tool is unavailable"
            ) from error

        if result.is_error:
            raise ProviderUnavailableError("Airport-resolution tool failed")

        try:
            return AirportResolution.model_validate(result.structured_content)
        except (AttributeError, TypeError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Airport-resolution tool returned an invalid response"
            ) from error

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

    async def search_flights(
        self, *, request: FlightSearchPreparationInput
    ) -> FlightSearchResponse:
        """Search flights through MCP and validate results or guidance."""

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
            payload = result.structured_content["result"]
            return FLIGHT_SEARCH_RESPONSE_ADAPTER.validate_python(payload)
        except (AttributeError, KeyError, TypeError, ValidationError) as error:
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

    async def search_places(self, *, request: PlaceSearchInput) -> PlaceSearchResponse:
        """Search places through MCP and validate results or guidance."""
        arguments = request.model_dump(mode="json", exclude_none=True)
        try:
            result = await self.mcp_server.call_tool(
                "search_places", arguments=arguments
            )
        except Exception as error:
            raise ProviderUnavailableError(
                "Place-search tool is unavailable"
            ) from error
        if result.is_error:
            raise ProviderUnavailableError("Place-search tool failed")

        try:
            payload = result.structured_content["result"]
            return PLACE_SEARCH_RESPONSE_ADAPTER.validate_python(payload)
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValidationError,
        ) as error:
            raise ProviderUnavailableError(
                "Place-search tool returned an invalid response"
            ) from error

    async def convert_currency(
        self,
        *,
        request: CurrencyConversionInput,
    ) -> CurrencyConversionResponse:
        """Convert currency through MCP and validate the result or guidance."""

        arguments = request.model_dump(mode="json")
        try:
            result = await self.mcp_server.call_tool(
                "convert_currency",
                arguments=arguments,
            )
        except Exception as error:
            raise ProviderUnavailableError(
                "Currency-conversion tool is unavailable"
            ) from error

        if result.is_error:
            raise ProviderUnavailableError("Currency-conversion tool failed")

        try:
            payload = result.structured_content["result"]
            return CURRENCY_CONVERSION_RESPONSE_ADAPTER.validate_python(payload)
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValidationError,
        ) as error:
            raise ProviderUnavailableError(
                "Currency-conversion tool returned an invalid response"
            ) from error
