"""Internal FastMCP server assembly."""

from fastmcp import FastMCP

from app.mcp.tools.weather import register_weather_tools
from app.providers.weather.client import WeatherProvider


def create_mcp_server(*, weather_provider: WeatherProvider) -> FastMCP:
    """Build the internal Travel MCP server."""

    server = FastMCP(
        name="Travel Assistant",
        instructions="Internal travel-provider tools. Return normalized data only.",
    )
    register_weather_tools(server, weather_provider=weather_provider)
    return server
