"""Run one live place search directly through Google Places."""

import argparse
import asyncio
import logging

import httpx

from app.config import get_settings
from app.observability.logging import configure_logging
from app.providers.locations.weatherapi_client import (
    WeatherApiLocationClient,
)
from app.providers.places.google_client import GooglePlacesClient
from app.providers.places.schemas import PlaceSearchInput
from app.services.place_search_service import PlaceSearchService

logger = logging.getLogger(__name__)


async def check_google_places(
    *,
    destination: str,
    interests: list[str],
    family_friendly: bool,
) -> None:
    """Run one validated and cost-bounded Google Places search."""

    request = PlaceSearchInput(
        destination=destination,
        interests=interests,
        family_friendly=family_friendly,
        max_results=3,
    )

    settings = get_settings()
    configure_logging(settings.log_level)

    async with httpx.AsyncClient() as http_client:
        location_provider = WeatherApiLocationClient(
            http_client=http_client,
            settings=settings,
        )
        place_provider = GooglePlacesClient(
            http_client=http_client,
            settings=settings,
        )
        service = PlaceSearchService(
            location_provider=location_provider,
            place_provider=place_provider,
        )

        result = await service.search_places(request=request)

    logger.info(
        "Google Places check completed",
        extra={
            "destination": result.location.display_name,
            "status": result.status,
            "places": [
                {
                    "provider_place_id": place.provider_place_id,
                    "name": place.name,
                    "categories": place.categories,
                    "address": place.address,
                    "latitude": place.latitude,
                    "longitude": place.longitude,
                    "source_urls": [str(url) for url in place.source_urls],
                }
                for place in result.places
            ],
            "result_message": result.message,
        },
    )


def parse_arguments() -> argparse.Namespace:
    """Parse one destination and optional place interests."""

    parser = argparse.ArgumentParser(
        description=("Check one live, cost-bounded Google Places search.")
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
        check_google_places(
            destination=arguments.destination,
            interests=arguments.interests,
            family_friendly=arguments.family_friendly,
        )
    )
