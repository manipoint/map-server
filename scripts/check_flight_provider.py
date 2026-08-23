"""Run one live flight search directly through Duffel."""

import argparse
import asyncio
from datetime import date

import httpx

from app.config import get_settings
from app.providers.flights.duffel_client import DuffelFlightClient
from app.providers.flights.schemas import FlightSearchInput


async def check_flight_provider(
    *,
    origin: str,
    destination: str,
    departure_date: date,
) -> None:
    """Run one validated live Duffel flight search."""

    request = FlightSearchInput(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        max_results=3,
    )
    settings = get_settings()

    async with httpx.AsyncClient() as http_client:
        provider = DuffelFlightClient(
            http_client=http_client,
            settings=settings,
        )
        result = await provider.search_flights(
            request=request,
        )

    print(result.model_dump_json(indent=2))


def parse_arguments() -> argparse.Namespace:
    """Parse the flight route supplied from the command line."""

    parser = argparse.ArgumentParser(
        description="Check the live Duffel flight provider."
    )
    parser.add_argument("origin", help="Origin three-letter IATA code")
    parser.add_argument(
        "destination",
        help="Destination three-letter IATA code",
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
        check_flight_provider(
            origin=arguments.origin,
            destination=arguments.destination,
            departure_date=arguments.departure_date,
        )
    )
