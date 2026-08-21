"""LangChain tools backed by the internal MCP client."""

from langchain_core.tools import BaseTool, StructuredTool

from app.mcp.client import TravelMcpClient
from app.mcp.schemas.weather import CurrentWeatherInput


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
