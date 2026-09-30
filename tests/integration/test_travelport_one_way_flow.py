"""Synthetic one-way flow through real graph, MCP, services and adapters."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastmcp import FastMCP

from app.config import Settings
from app.graph.tools import create_flight_search_tool
from app.mcp.client import TravelMcpClient
from app.mcp.tools.flights import register_flight_tools
from app.providers.airports.local_provider import LocalAirportProvider
from app.providers.flights.local_metadata_provider import LocalFlightMetadataProvider
from app.providers.flights.schemas import FlightSearchResult
from app.providers.travelport.auth_client import TravelportAuthClient
from app.providers.travelport.flight_client import TravelportFlightClient
from app.services.airport_resolution_service import AirportResolutionService
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationService,
)
from app.services.flight_search_service import FlightSearchService

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "travelport"
NOW = datetime(2027, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("round_trip", [False, True])
@pytest.mark.parametrize("scenario", ["offers", "empty", "ambiguous", "unknown"])
def test_search_flow_with_only_http_mocked(scenario: str, round_trip: bool) -> None:
    async def run():
        metadata = await LocalFlightMetadataProvider.from_file(
            path=FIXTURES / "flight_metadata.json",
        )
        airports = await LocalAirportProvider.from_file(
            path=FIXTURES / "airport_directory.json",
        )
        assert airports.airport_codes.issubset(metadata.airport_codes)
        config = Settings(
            _env_file=None,
            flight_provider="travelport",
            flight_metadata_path=str(FIXTURES / "flight_metadata.json"),
            airport_directory_path=str(FIXTURES / "airport_directory.json"),
            travelport_username="fixture-user",
            travelport_password="fixture-password",
            travelport_client_id="fixture-client",
            travelport_client_secret="fixture-secret",
            travelport_pcc_core="TEST",
        )
        calls = []
        responses = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            assert request.method == "POST"
            if str(request.url) == config.travelport_auth_url:
                response = httpx.Response(200, json={"access_token": "fixture-token"})
            else:
                assert str(request.url) == (
                    config.travelport_air_base_url
                    + "/catalog/search/catalogproductofferings"
                )
                assert request.headers["Authorization"] == "Bearer fixture-token"
                payload = json.loads(request.content)["CatalogProductOfferingsRequest"]
                routes = payload["SearchCriteriaFlight"]
                assert len(routes) == (2 if round_trip else 1)
                route = routes[0]
                if round_trip:
                    assert routes[1]["From"]["value"] == "LAX"
                    assert routes[1]["To"]["value"] == "JFK"
                    assert routes[1]["departureDate"] == "2027-11-15"
                assert route["From"]["value"] == "JFK"
                assert route["To"]["value"] == "LAX"
                assert route["departureDate"] == "2027-11-08"
                if scenario == "empty":
                    response = httpx.Response(
                        200,
                        json={
                            "CatalogProductOfferingsResponse": {
                                "CatalogProductOfferings": {
                                    "CatalogProductOffering": []
                                },
                            },
                        },
                    )
                else:
                    response = httpx.Response(
                        200,
                        content=(
                            FIXTURES
                            / (
                                "round_trip_response.json"
                                if round_trip
                                else "one_way_response.json"
                            )
                        ).read_bytes(),
                        headers={"Content-Type": "application/json"},
                    )
            responses.append(response)
            return response

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            provider = TravelportFlightClient(
                http_client=http,
                settings=config,
                metadata_provider=metadata,
                auth_client=TravelportAuthClient(http_client=http, settings=config),
                clock=lambda: NOW,
            )
            service = FlightSearchPreparationService(
                airport_resolution_service=AirportResolutionService(
                    airport_provider=airports
                ),
                flight_search_service=FlightSearchService(
                    flight_provider=provider,
                    supports_round_trip=True,
                    clock=lambda: NOW,
                ),
            )
            server = FastMCP(name="Synthetic one-way flow")
            register_flight_tools(server, flight_search_service=service)
            tool = create_flight_search_tool(
                mcp_client=TravelMcpClient(mcp_server=server)
            )
            arguments = {
                "origin": "New York, United States",
                "destination": {
                    "ambiguous": "Los Angeles",
                    "unknown": "Unknown City",
                }.get(scenario, "lax"),
                "departure_date": "2027-11-08",
            }
            if round_trip:
                arguments["return_date"] = "2027-11-15"
            result = await tool.ainvoke(arguments)
            if scenario in {"ambiguous", "unknown"}:
                assert result["status"] == "airport_resolution_required"
                assert result["origin"]["iata_code"] == "JFK"
                assert result["destination"]["status"] == (
                    "selection_required" if scenario == "ambiguous" else "not_found"
                )
                if scenario == "ambiguous":
                    assert {
                        o["iata_code"] for o in result["destination"]["options"]
                    } == {"BUR", "LAX"}
                assert calls == []
            else:
                mapped = FlightSearchResult.model_validate(result)
                assert mapped.searched_at == NOW
                if scenario == "empty":
                    assert mapped.status.value == "no_offers"
                    assert mapped.offers == []
                else:
                    assert mapped.status.value == "offers_available"
                    (offer,) = mapped.offers
                    assert offer.total_price == Decimal(
                        "486.44" if round_trip else "243.22"
                    )
                    assert offer.currency == "USD"
                    assert offer.traveler_count == 1
                    assert (offer.return_itinerary is not None) is round_trip
                    if round_trip:
                        assert (
                            offer.return_itinerary.segments[0].departure_airport
                            == "LAX"
                        )
                        assert offer.return_itinerary.duration_minutes == 300
                    assert offer.outbound.duration_minutes == 371
                    assert offer.outbound.stops == 0
                    (segment,) = offer.outbound.segments
                    assert segment.departure_at == datetime(
                        2027, 11, 8, 11, 30, tzinfo=UTC
                    )
                    assert segment.arrival_at == datetime(
                        2027, 11, 8, 17, 41, tzinfo=UTC
                    )
                    assert segment.marketing_carrier_name == "Fixture Airline"
                await tool.ainvoke(arguments)
                assert len(calls) == 3  # One token acquisition for two searches.
            assert not http.is_closed
        assert http.is_closed
        assert all(response.is_closed for response in responses)

    asyncio.run(run())
