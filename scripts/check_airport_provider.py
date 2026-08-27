"""Run one live airport-code resolution through Duffel Places."""

import argparse
import asyncio
import logging

import httpx

from app.config import get_settings
from app.observability.logging import configure_logging
from app.providers.airports.duffel_client import DuffelAirportClient
from app.providers.airports.schemas import AirportSearchInput
from app.services.airport_resolution_service import AirportResolutionService

logger = logging.getLogger(__name__)


async def check_airport_provider(*, query: str) -> None:
    """Resolve one airport or city query with a bounded live lookup."""

    request = AirportSearchInput(query=query, max_results=5)
    settings = get_settings()
    configure_logging(settings.log_level)

    async with httpx.AsyncClient() as http_client:
        provider = DuffelAirportClient(
            http_client=http_client,
            settings=settings,
        )
        service = AirportResolutionService(airport_provider=provider)
        result = await service.resolve_airport(request=request)

    logger.info(
        "Airport resolution check completed",
        extra={
            "airport_query": result.query,
            "resolution_status": result.status,
            "iata_code": result.iata_code,
            "options": [
                {
                    "iata_code": option.iata_code,
                    "location_type": option.location_type,
                    "display_name": option.display_name,
                }
                for option in result.options
            ],
        },
    )


def parse_arguments() -> argparse.Namespace:
    """Parse one airport code, airport name, or city name."""

    parser = argparse.ArgumentParser(
        description="Check live Duffel airport-code resolution."
    )
    parser.add_argument(
        "query",
        help="Three-letter IATA code, airport name, or city name",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(check_airport_provider(query=arguments.query))
