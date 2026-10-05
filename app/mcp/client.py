"""Graph-facing MCP client."""

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

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

Result = TypeVar("Result")
AIRPORT_RESPONSE_ADAPTER = TypeAdapter(AirportResolution)
WEATHER_RESPONSE_ADAPTER = TypeAdapter(CurrentWeather)
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

    async def _call(
        self,
        *,
        name: str,
        label: str,
        request: BaseModel,
        adapter: TypeAdapter[Result],
        envelope: bool = True,
    ) -> Result:
        try:
            result = await self.mcp_server.call_tool(
                name, arguments=request.model_dump(mode="json", exclude_none=True)
            )
        except Exception as error:
            raise ProviderUnavailableError(f"{label} tool is unavailable") from error
        try:
            if result.is_error:
                raise ProviderUnavailableError(f"{label} tool failed")
            payload = (
                result.structured_content["result"]
                if envelope
                else result.structured_content
            )
            return adapter.validate_python(payload)
        except (AttributeError, KeyError, TypeError, ValidationError) as error:
            raise ProviderUnavailableError(
                f"{label} tool returned an invalid response"
            ) from error

    async def resolve_airport(
        self, *, request: AirportSearchInput
    ) -> AirportResolution:
        return await self._call(
            name="resolve_airport",
            label="Airport-resolution",
            request=request,
            adapter=AIRPORT_RESPONSE_ADAPTER,
            envelope=False,
        )

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        return await self._call(
            name="get_current_weather",
            label="Current-weather",
            request=CurrentWeatherInput(city=city),
            adapter=WEATHER_RESPONSE_ADAPTER,
            envelope=False,
        )

    async def search_flights(
        self, *, request: FlightSearchPreparationInput
    ) -> FlightSearchResponse:
        return await self._call(
            name="search_flights",
            label="Flight-search",
            request=request,
            adapter=FLIGHT_SEARCH_RESPONSE_ADAPTER,
        )

    async def search_hotels(self, *, request: HotelSearchInput) -> HotelSearchResponse:
        return await self._call(
            name="search_hotels",
            label="Hotel-search",
            request=request,
            adapter=HOTEL_SEARCH_RESPONSE_ADAPTER,
        )

    async def search_places(self, *, request: PlaceSearchInput) -> PlaceSearchResponse:
        return await self._call(
            name="search_places",
            label="Place-search",
            request=request,
            adapter=PLACE_SEARCH_RESPONSE_ADAPTER,
        )

    async def convert_currency(
        self, *, request: CurrencyConversionInput
    ) -> CurrencyConversionResponse:
        return await self._call(
            name="convert_currency",
            label="Currency-conversion",
            request=request,
            adapter=CURRENCY_CONVERSION_RESPONSE_ADAPTER,
        )
