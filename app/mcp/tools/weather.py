"""Current conditions and dated forecast MCP tools."""

from datetime import date
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from app.mcp.schemas.weather import CurrentWeatherInput, WeatherForecastInput
from app.providers.weather.client import WeatherProvider
from app.providers.weather.schemas import CurrentWeather, WeatherForecast


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

    @server.tool(
        name="get_weather_forecast",
        description=(
            "Return verified hourly and daily destination weather for requested dates, "
            "including local time and rain/snow probability percentages."
        ),
    )
    async def get_weather_forecast(
        city: Annotated[str, Field(max_length=120, min_length=1)],
        start_date: date,
        end_date: date,
    ) -> WeatherForecast:
        request = WeatherForecastInput(
            city=city, start_date=start_date, end_date=end_date
        )
        return await weather_provider.get_forecast(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
        )
