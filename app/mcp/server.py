"""Internal FastMCP server assembly."""

from fastmcp import FastMCP

from app.mcp.tools.flights import register_flight_tools
from app.mcp.tools.weather import register_weather_tools
from app.providers.flights.client import FlightProvider
from app.providers.weather.client import WeatherProvider


def create_mcp_server(
    *, weather_provider: WeatherProvider, flight_provider: FlightProvider | None = None
) -> FastMCP:
    """Build the internal Travel MCP server."""

    server = FastMCP(
        name="Travel Assistant",
        instructions="Internal travel-provider tools. Return normalized data only.",
    )
    register_weather_tools(server, weather_provider=weather_provider)

    if flight_provider is not None:
        register_flight_tools(server=server, flight_provider=flight_provider)
    return server
