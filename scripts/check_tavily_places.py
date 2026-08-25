"""Inspect one live Tavily place-discovery response."""

import argparse
import asyncio
import logging

import httpx

from app.config import get_settings
from app.observability.logging import configure_logging
from app.providers.locations.weatherapi_client import WeatherApiLocationClient
from app.providers.places.schemas import PlaceSearchInput
from app.providers.places.tavily_client import TavilySearchClient
from app.providers.places.tavily_mapper import build_tavily_place_search
from app.services.location_selection import select_resolved_location

LOCATION_CANDIDATE_LIMIT = 5

logger = logging.getLogger(__name__)


async def check_tavily_places(
    *,
    destination: str,
    interests: list[str],
) -> None:
    """Resolve a destination and log bounded Tavily result metadata."""

    request = PlaceSearchInput(
        destination=destination,
        interests=interests,
        max_results=5,
    )
    settings = get_settings()
    configure_logging(settings.log_level)

    async with httpx.AsyncClient() as http_client:
        location_client = WeatherApiLocationClient(
            http_client=http_client,
            settings=settings,
        )
        tavily_client = TavilySearchClient(
            http_client=http_client,
            settings=settings,
        )
        candidates = await location_client.search_locations(
            query=request.destination,
            max_results=LOCATION_CANDIDATE_LIMIT,
        )
        location = select_resolved_location(
            query=request.destination,
            candidates=candidates,
        )
        provider_request = build_tavily_place_search(
            request=request,
            location=location,
        )
        response = await tavily_client.search(request=provider_request)

    logger.info(
        "Tavily places check completed",
        extra={
            "destination": location.display_name,
            "tavily_request_id": response.request_id,
            "results": [
                {
                    "title": item.title,
                    "url": str(item.url),
                    "score": item.score,
                }
                for item in response.results
            ],
        },
    )


def parse_arguments() -> argparse.Namespace:
    """Parse one destination and optional place interests."""

    parser = argparse.ArgumentParser(
        description="Inspect one live bounded Tavily place-discovery response."
    )
    parser.add_argument("destination", help="Destination city and country or region")
    parser.add_argument(
        "interests",
        nargs="*",
        help="Optional interests such as museums or parks",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(
        check_tavily_places(
            destination=arguments.destination,
            interests=arguments.interests,
        )
    )
