"""Explicitly opted-in, preproduction-only round-trip search smoke test."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.common.exceptions import (
    InvalidTravelDateError,
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.domain.flights import FlightSearchStatus
from app.providers.flights.local_metadata_provider import LocalFlightMetadataProvider
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult
from app.providers.travelport.auth_client import TravelportAuthClient
from app.providers.travelport.flight_client import TravelportFlightClient
from app.services.flight_search_service import FlightSearchService


def _diagnostic(stage: str, outcome: str, *, http_status: int | None = None) -> None:
    """Only locally chosen labels and numeric status; never payloads or URLs."""
    event: dict[str, str | int] = {"stage": stage, "outcome": outcome}
    if http_status is not None:
        event["http_status"] = http_status
    print(json.dumps(event), file=sys.stderr)


async def run_sandbox_search(
    *, settings: Settings, request: FlightSearchInput
) -> FlightSearchResult:
    """Use real provider/service code, without startup, database, MCP or LLMs."""
    _diagnostic("sandbox_configuration", "started")
    if settings.travelport_environment != "preproduction":
        raise ProviderConfigurationError("Sandbox requires preproduction")
    if settings.flight_provider != "travelport" or not settings.flight_metadata_path:
        raise ProviderConfigurationError("Travelport and metadata must be configured")
    if request.return_date is None:
        raise ProviderConfigurationError("Sandbox requires a return date")
    _diagnostic("metadata_load", "started")
    metadata = await LocalFlightMetadataProvider.from_file(
        path=Path(settings.flight_metadata_path)
    )
    _diagnostic("metadata_load", "passed")

    def stage_for(request: httpx.Request) -> str:
        return (
            "authentication"
            if str(request.url) == settings.travelport_auth_url
            else "flight_search"
        )

    async def on_request(request: httpx.Request) -> None:
        _diagnostic(stage_for(request), "request_started")

    async def on_response(response: httpx.Response) -> None:
        _diagnostic(
            stage_for(response.request),
            "response_received",
            http_status=response.status_code,
        )

    async with httpx.AsyncClient(
        follow_redirects=False,
        event_hooks={"request": [on_request], "response": [on_response]},
    ) as http:
        provider = TravelportFlightClient(
            http_client=http,
            auth_client=TravelportAuthClient(http_client=http, settings=settings),
            metadata_provider=metadata,
            settings=settings,
        )
        service = FlightSearchService(
            flight_provider=provider, supports_round_trip=True
        )
        _diagnostic("flight_service", "started")
        result = await service.search_flights(request=request)
        _diagnostic("response_validation", "passed")
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--origin", required=True, help="Origin airport IATA code")
    parser.add_argument(
        "--destination", required=True, help="Destination airport IATA code"
    )
    parser.add_argument("--departure", required=True, help="Future date: YYYY-MM-DD")
    parser.add_argument("--return-date", required=True, help="Return date: YYYY-MM-DD")
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--currency", default="USD")
    args = parser.parse_args(argv)
    if not args.run_sandbox:
        print("No network call made. Add --run-sandbox to opt in.", file=sys.stderr)
        return 2
    try:
        _diagnostic("request_validation", "started")
        request = FlightSearchInput(
            origin=args.origin,
            destination=args.destination,
            departure_date=args.departure,
            return_date=args.return_date,
            adults=args.adults,
            currency=args.currency,
            max_results=3,
        )
        # Loaded only after explicit opt-in; never print settings or validation inputs.
        _diagnostic("settings_load", "started")
        settings = Settings()
        _diagnostic("settings_load", "passed")
        result = asyncio.run(run_sandbox_search(settings=settings, request=request))
        if result.status is FlightSearchStatus.NO_OFFERS:
            print(
                "INCONCLUSIVE: search succeeded, but no matching round-trip offers were returned."
            )
            return 3
        if result.status is not FlightSearchStatus.OFFERS_AVAILABLE:
            print("INCONCLUSIVE: search requires group assistance.")
            return 3
        if any(offer.return_itinerary is None for offer in result.offers):
            print("FAIL: response is missing a return itinerary.", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "status": "passed",
                    "environment": "preproduction",
                    "offer_count": len(result.offers),
                    "offers": [
                        {
                            "total_price": str(offer.total_price),
                            "currency": offer.currency,
                            "traveler_count": offer.traveler_count,
                            "outbound_segments": len(offer.outbound.segments),
                            "return_segments": len(offer.return_itinerary.segments),
                        }
                        for offer in result.offers
                    ],
                }
            )
        )
        return 0
    except (ValidationError, ProviderConfigurationError, InvalidTravelDateError):
        print(
            "FAIL: configuration/input or provider access rejected. Check the stage and HTTP status above.",
            file=sys.stderr,
        )
        return 2
    except ProviderUnavailableError:
        print(
            "FAIL: provider transport, metadata resolution or response validation failed. Check the stage and HTTP status above.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        # CLI boundary: SDK exceptions can contain credentials or provider payloads.
        print(
            "FAIL: sandbox search could not complete; no exception details were printed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
