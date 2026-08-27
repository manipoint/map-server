"""Run one live flight request through MCP and LangGraph."""

import argparse
import asyncio
import logging
from datetime import date

import httpx
from langchain_core.messages import HumanMessage

from app.config import get_settings
from app.graph.builder import build_travel_graph
from app.graph.subgraphs.model_gateway import build_model_gateway
from app.graph.tools import create_current_weather_tool, create_flight_search_tool
from app.mcp.client import TravelMcpClient
from app.mcp.server import create_mcp_server
from app.observability.logging import configure_logging
from app.providers.airports.duffel_client import DuffelAirportClient
from app.providers.flights.duffel_client import DuffelFlightClient
from app.providers.weather.client import WeatherApiClient
from app.services.airport_resolution_service import AirportResolutionService
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationInput,
    FlightSearchPreparationService,
)
from app.services.flight_search_service import FlightSearchService

logger = logging.getLogger(__name__)


async def check_flight_graph(
    *, origin: str, destination: str, departure_date: date
) -> None:
    """Run one flight request through the complete travel graph."""

    request = FlightSearchPreparationInput(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        adults=1,
        max_results=3,
    )
    settings = get_settings()
    configure_logging(settings.log_level)

    async with httpx.AsyncClient() as http_client:
        weather_provider = WeatherApiClient(http_client=http_client, settings=settings)
        flight_provider = DuffelFlightClient(
            http_client=http_client,
            settings=settings,
        )
        airport_provider = DuffelAirportClient(
            http_client=http_client,
            settings=settings,
        )
        airport_resolution_service = AirportResolutionService(
            airport_provider=airport_provider,
        )
        flight_search_service = FlightSearchService(
            flight_provider=flight_provider,
        )
        flight_search_preparation_service = FlightSearchPreparationService(
            airport_resolution_service=airport_resolution_service,
            flight_search_service=flight_search_service,
        )
        mcp_server = create_mcp_server(
            weather_provider=weather_provider,
            flight_search_service=flight_search_preparation_service,
        )
        mcp_client = TravelMcpClient(mcp_server=mcp_server)
        tools = [
            create_current_weather_tool(
                mcp_client=mcp_client,
            ),
            create_flight_search_tool(
                mcp_client=mcp_client,
            ),
        ]
        model_gateway = build_model_gateway(settings=settings, tools=tools)
        graph = build_travel_graph(
            model_gateway=model_gateway,
            tools=tools,
            max_tool_rounds=settings.max_tool_rounds,
        )
        result = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            "Find up to 3 one-way economy flight offers "
                            f"from {request.origin} to "
                            f"{request.destination}, departing "
                            f"{request.departure_date.isoformat()}, "
                            "for 1 adult. Use the flight search tool. "
                            "Summarize prices, airlines, flight numbers, "
                            "and departure times."
                        )
                    )
                ],
                "locale": "en-PK",
            }
        )

    logger.info(
        "Flight graph check completed",
        extra={
            "origin": request.origin,
            "destination": request.destination,
            "departure_date": request.departure_date.isoformat(),
            "assistant_response": result["assistant_response"],
        },
    )


def parse_arguments() -> argparse.Namespace:
    """Parse the flight route supplied from the command line."""

    parser = argparse.ArgumentParser(
        description="Check the live flight-search LangGraph flow."
    )
    parser.add_argument(
        "origin",
        help="Origin IATA code, airport name, or city",
    )
    parser.add_argument(
        "destination",
        help="Destination IATA code, airport name, or city",
    )

    parser.add_argument(
        "departure_date",
        type=date.fromisoformat,
        help="Departure date in YYYY-MM-DD format",
    )

    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(
        check_flight_graph(
            origin=arguments.origin,
            destination=arguments.destination,
            departure_date=arguments.departure_date,
        )
    )
