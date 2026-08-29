"""Run one live flight request through MCP and LangGraph."""

import argparse
import asyncio
import logging
from datetime import date

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

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


def build_graph_message(
    *,
    request: FlightSearchPreparationInput,
    message: str | None,
) -> str:
    """Return a bounded default or caller-supplied graph prompt."""

    default_message = (
        "Find up to 3 one-way economy flight offers "
        f"from {request.origin} to {request.destination}, departing "
        f"{request.departure_date.isoformat()}, "
        "for 1 adult. Use the flight search tool. "
        "Summarize prices, airlines, flight numbers, and departure times."
    )
    resolved_message = message.strip() if message is not None else default_message
    if not 1 <= len(resolved_message) <= 2000:
        raise ValueError("message must contain between 1 and 2000 characters")
    return resolved_message


def find_flight_tool_arguments(
    messages: list[BaseMessage],
) -> dict[str, object]:
    """Return the first model-selected flight route for manual verification."""

    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls:
            if tool_call["name"] == "search_flights":
                return dict(tool_call["args"])
    raise RuntimeError("model did not call search_flights")


async def check_flight_graph(
    *,
    origin: str,
    destination: str,
    departure_date: date,
    message: str | None = None,
) -> None:
    """Run one structured or natural-language request through the flight graph."""

    request = FlightSearchPreparationInput(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        adults=1,
        max_results=3,
    )
    graph_message = build_graph_message(request=request, message=message)
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
                "messages": [HumanMessage(content=graph_message)],
                "locale": "en-PK",
            }
        )

    tool_arguments = find_flight_tool_arguments(result["messages"])
    logger.info(
        "Flight graph check completed",
        extra={
            "expected_origin": request.origin,
            "expected_destination": request.destination,
            "model_origin": tool_arguments.get("origin"),
            "model_destination": tool_arguments.get("destination"),
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
    parser.add_argument(
        "--message",
        help=(
            "Optional raw natural-language prompt; positional route remains the "
            "expected direction logged for comparison"
        ),
    )

    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(
        check_flight_graph(
            origin=arguments.origin,
            destination=arguments.destination,
            departure_date=arguments.departure_date,
            message=arguments.message,
        )
    )
