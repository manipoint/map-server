"""Internal FastMCP server assembly."""

from fastmcp import FastMCP

from app.mcp.tools.currency import register_currency_tools
from app.mcp.tools.flights import register_flight_tools
from app.mcp.tools.hotels import register_hotel_tools
from app.mcp.tools.places import register_place_tools
from app.mcp.tools.weather import register_weather_tools
from app.providers.currency.client import CurrencyProvider
from app.providers.flights.client import FlightProvider
from app.providers.weather.client import WeatherProvider
from app.services.hotel_search_service import HotelSearchService
from app.services.place_search_service import PlaceSearchService


def create_mcp_server(
    *,
    weather_provider: WeatherProvider,
    flight_provider: FlightProvider | None = None,
    hotel_search_service: HotelSearchService | None = None,
    place_search_service: PlaceSearchService | None = None,
    currency_provider: CurrencyProvider | None = None,
) -> FastMCP:
    """Build the internal Travel MCP server."""

    server = FastMCP(
        name="Travel Assistant",
        instructions="Internal travel-provider tools. Return normalized data only.",
    )
    register_weather_tools(server, weather_provider=weather_provider)

    if flight_provider is not None:
        register_flight_tools(server=server, flight_provider=flight_provider)

    if hotel_search_service is not None:
        register_hotel_tools(server=server, hotel_search_service=hotel_search_service)

    if place_search_service is not None:
        register_place_tools(
            server,
            place_search_service=place_search_service,
        )

    if currency_provider is not None:
        register_currency_tools(server, currency_provider=currency_provider)
    return server
