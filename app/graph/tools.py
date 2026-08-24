"""LangChain tools backed by the internal MCP client."""

from langchain_core.tools import BaseTool, StructuredTool

from app.mcp.client import TravelMcpClient
from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.flights.schemas import FlightSearchInput
from app.providers.hotels.schemas import HotelSearchInput


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


def create_flight_search_tool(*, mcp_client: TravelMcpClient) -> BaseTool:
    """Create the model-facing flight-search tool."""

    async def search_flights(**arguments: object) -> dict[str, object]:
        request = FlightSearchInput.model_validate(arguments)
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
        args_schema=FlightSearchInput,
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
