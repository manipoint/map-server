"""LangChain tools backed by the internal MCP client."""

from langchain_core.tools import BaseTool, StructuredTool

from app.mcp.client import TravelMcpClient
from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.flights.schemas import FlightSearchInput


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
            "Get current verified weather for a city. "
            "Use this when the user asks about current weather."
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
            "Search current flight offers for a route and passenger group. "
            "Provide each child's exact age and classify infants as either "
            "travelling with a seat or on an adult's lap. "
            "The returned total price covers every requested traveler. "
            "Use this tool only to search flights; it does not book them."
        ),
        args_schema=FlightSearchInput,
    )
