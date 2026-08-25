"""Run one live places request through MCP and LangGraph."""

import argparse
import asyncio
import logging

import httpx
from langchain_core.messages import HumanMessage

from app.config import get_settings
from app.graph.builder import build_travel_graph
from app.graph.subgraphs.model_gateway import build_model_gateway
from app.graph.tools import create_place_search_tool
from app.mcp.client import TravelMcpClient
from app.mcp.server import create_mcp_server
from app.observability.logging import configure_logging
from app.providers.locations.weatherapi_client import (
    WeatherApiLocationClient,
)
from app.providers.places.google_client import GooglePlacesClient
from app.providers.places.schemas import PlaceSearchInput
from app.providers.weather.client import WeatherApiClient
from app.services.place_search_service import PlaceSearchService

logger = logging.getLogger(__name__)


async def check_places_graph(
    *, destination: str, interests: list[str], family_friendly: bool
) -> None:
    """Run one cost-bounded places request through the travel graph."""
    request = PlaceSearchInput(
        destination=destination,
        interests=interests,
        family_friendly=family_friendly,
        max_results=3,
    )
    settings = get_settings()
    configure_logging(settings.log_level)

    async with httpx.AsyncClient() as http_client:
        weather_provider = WeatherApiClient(http_client=http_client, settings=settings)
        location_provider = WeatherApiLocationClient(
            http_client=http_client, settings=settings
        )
        place_provider = GooglePlacesClient(http_client=http_client, settings=settings)
        place_service = PlaceSearchService(
            location_provider=location_provider, place_provider=place_provider
        )
        mcp_server = create_mcp_server(
            weather_provider=weather_provider, place_search_service=place_service
        )
        mcp_client = TravelMcpClient(mcp_server=mcp_server)
        place_tool = create_place_search_tool(mcp_client=mcp_client)
        tools = [place_tool]
        model_gateway = build_model_gateway(settings=settings, tools=tools)

        graph = build_travel_graph(
            model_gateway=model_gateway,
            tools=tools,
            max_tool_rounds=settings.max_tool_rounds,
        )
        interests_text = (
            ", ".join(request.interests) if request.interests else "general sightseeing"
        )
        family_text = (
            " Prefer family-friendly places." if request.family_friendly else ""
        )
        result = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            f"Find up to 3 places to visit in "
                            f"{request.destination}. Interests: "
                            f"{interests_text}.{family_text} "
                            "Use search_places exactly once. "
                            "Summarize names, categories, addresses, "
                            "and Google Maps source links."
                        )
                    )
                ],
                "locale": "en-PK",
            }
        )
        logger.info(
            "Places graph check completed",
            extra={
                "destination": request.destination,
                "interests": request.interests,
                "family_friendly": request.family_friendly,
                "assistant_response": result["assistant_response"],
            },
        )


def parse_arguments() -> argparse.Namespace:
    """Parse one destination and optional interests."""

    parser = argparse.ArgumentParser(
        description=("Check one live, cost-bounded places LangGraph flow.")
    )
    parser.add_argument(
        "destination",
        help="Destination city with country or region",
    )
    parser.add_argument(
        "interests",
        nargs="*",
        help="Optional interests such as museums or parks",
    )
    parser.add_argument(
        "--family-friendly",
        action="store_true",
        help="Prefer family-friendly attractions",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()

    asyncio.run(
        check_places_graph(
            destination=arguments.destination,
            interests=arguments.interests,
            family_friendly=arguments.family_friendly,
        )
    )
