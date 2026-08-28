"""LangChain tools backed by the internal MCP client."""

from langchain_core.tools import BaseTool, StructuredTool

from app.graph.schemas.itineraries import GeneratedItinerary
from app.mcp.client import TravelMcpClient
from app.mcp.schemas.flights import FlightSearchPreparationInput
from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.airports.schemas import AirportSearchInput
from app.providers.currency.schemas import CurrencyConversionInput
from app.providers.hotels.schemas import HotelSearchInput
from app.providers.places.schemas import PlaceSearchInput

ITINERARY_SUBMISSION_TOOL_NAME = "submit_itinerary"


def create_current_weather_tool(
    *,
    mcp_client: TravelMcpClient,
) -> BaseTool:
    """Create the model-facing current-weather tool."""

    async def get_current_weather(city: str) -> dict[str, object]:
        weather = await mcp_client.get_current_weather(city=city)
        return weather.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=get_current_weather,
        name="get_current_weather",
        description=(
            "Get verified current conditions for one city. "
            "This is not a forecast or historical-weather tool."
        ),
        args_schema=CurrentWeatherInput,
    )


def create_airport_resolution_tool(
    *,
    mcp_client: TravelMcpClient,
) -> BaseTool:
    """Create the model-facing airport-resolution tool."""

    async def resolve_airport(**arguments: object) -> dict[str, object]:
        request = AirportSearchInput.model_validate(arguments)
        result = await mcp_client.resolve_airport(request=request)
        return result.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=resolve_airport,
        name="resolve_airport",
        description=(
            "Resolve a city or airport name to a normalized IATA code before "
            "flight search. Direct IATA codes require no provider lookup. "
            "If choices are returned, ask the user to select one; never guess."
        ),
        args_schema=AirportSearchInput,
    )


def create_flight_search_tool(*, mcp_client: TravelMcpClient) -> BaseTool:
    """Create the model-facing flight-search tool."""

    async def search_flights(**arguments: object) -> dict[str, object]:
        request = FlightSearchPreparationInput.model_validate(arguments)
        result = await mcp_client.search_flights(request=request)
        return result.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=search_flights,
        name="search_flights",
        description=(
            "Search live flight offers by route, dates, cabin, and party. "
            "Provide exact child ages and classify infants as seated or on-lap. "
            "Returned totals cover the whole party. Search only; no booking."
        ),
        args_schema=FlightSearchPreparationInput,
    )


def create_hotel_search_tool(*, mcp_client: TravelMcpClient) -> BaseTool:
    """Create the model-facing hotel-search tool."""

    async def search_hotels(**arguments: object) -> dict[str, object]:
        request = HotelSearchInput.model_validate(arguments)
        result = await mcp_client.search_hotels(request=request)
        return result.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=search_hotels,
        name="search_hotels",
        description=(
            "Search live hotel availability by destination, dates, rooms, and guests. "
            "Provide every child's exact age and include country or region when known. "
            "Prices are provisional. Search only; no booking."
        ),
        args_schema=HotelSearchInput,
    )


def create_place_search_tool(*, mcp_client: TravelMcpClient) -> BaseTool:
    """Create the model-facing place-discovery tool."""

    async def search_places(**arguments: object) -> dict[str, object]:
        request = PlaceSearchInput.model_validate(arguments)
        result = await mcp_client.search_places(request=request)
        return result.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=search_places,
        name="search_places",
        description=(
            "Find verified attractions and activities for one destination. "
            "Use interests such as museums, parks, history, food, or family "
            "activities when the user provides them. Include country or "
            "region when known. Returns at most five results. Discovery "
            "only; no booking."
        ),
        args_schema=PlaceSearchInput,
    )


def create_currency_conversion_tool(*, mcp_client: TravelMcpClient) -> BaseTool:
    """Create the model-facing currency-conversion tool."""

    async def convert_currency(**arguments: object) -> dict[str, object]:
        request = CurrencyConversionInput.model_validate(arguments)
        result = await mcp_client.convert_currency(request=request)
        return result.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=convert_currency,
        name="convert_currency",
        description=(
            "Convert one amount using a verified reference exchange rate. "
            "Use three-letter base and quote currency codes. Reference only; "
            "not a payment or booking quote."
        ),
        args_schema=CurrencyConversionInput,
    )


def create_itinerary_submission_tool() -> BaseTool:
    """Create a side-effect-free itinerary submission tool."""

    async def submit_itinerary(**arguments: object) -> dict[str, object]:
        itinerary = GeneratedItinerary.model_validate(arguments)
        return itinerary.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=submit_itinerary,
        name=ITINERARY_SUBMISSION_TOOL_NAME,
        description=(
            "Submit the final day-by-day itinerary for the active trip. "
            "Call only after required travel searches are complete. "
            "Do not call for general questions or standalone searches."
        ),
        args_schema=GeneratedItinerary,
    )
