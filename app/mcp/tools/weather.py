"""Current-weather MCP tool registration."""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.weather.client import WeatherProvider
from app.providers.weather.schemas import CurrentWeather


def register_weather_tools(
    server: FastMCP,
    *,
    weather_provider: WeatherProvider,
) -> None:
    """Register normalized weather tools on one MCP server."""

    @server.tool(
        name="get_current_weather",
        description=(
            "Return current normalized weather for a city. "
            "Use only when the user asks for current weather."
        ),
    )
    async def get_current_weather(
        city: Annotated[str, Field(max_length=120, min_length=1)],
    ) -> CurrentWeather:
        request = CurrentWeatherInput(city=city)
        return await weather_provider.get_current_weather(city=request.city)
