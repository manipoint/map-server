"""Flight capabilities are enforced before airport or provider work."""

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from app.common.exceptions import UnsupportedFlightRequestError
from app.domain.flights import ONE_WAY_ONLY_MESSAGE
from app.graph.tools import create_flight_search_tool
from app.mcp.client import TravelMcpClient
from app.mcp.tools.flights import register_flight_tools
from app.providers.airports.schemas import AirportResolution
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationInput,
    FlightSearchPreparationService,
)
from app.services.flight_search_service import FlightSearchService

NOW = datetime(2026, 9, 28, tzinfo=UTC)


@pytest.mark.parametrize("supports_round_trip", [False, True])
@pytest.mark.parametrize("return_date", [None, date(2027, 11, 10)])
def test_capability_through_graph_and_mcp(supports_round_trip, return_date):
    async def run():
        provider = AsyncMock()
        provider.search_flights.return_value = FlightSearchResult(
            status="no_offers",
            searched_at=NOW,
        )
        airport = AsyncMock()
        airport.resolve_airport.side_effect = [
            AirportResolution(status="resolved", query="LHE", iata_code="LHE"),
            AirportResolution(status="resolved", query="JFK", iata_code="JFK"),
        ]
        service = FlightSearchService(
            flight_provider=provider,
            clock=lambda: NOW,
            supports_round_trip=supports_round_trip,
        )
        preparation = FlightSearchPreparationService(
            airport_resolution_service=airport,
            flight_search_service=service,
        )
        server = FastMCP(name="Capability test")
        register_flight_tools(server, flight_search_service=preparation)
        tool = create_flight_search_tool(mcp_client=TravelMcpClient(mcp_server=server))
        request = FlightSearchPreparationInput(
            origin="LHE",
            destination="JFK",
            departure_date=date(2027, 11, 7),
            return_date=return_date,
        )
        result = await tool.ainvoke(request.model_dump(mode="json"))
        if return_date is not None and not supports_round_trip:
            assert result == {
                "status": "unsupported_request",
                "message": ONE_WAY_ONLY_MESSAGE,
            }
            airport.resolve_airport.assert_not_awaited()
            provider.search_flights.assert_not_awaited()
        else:
            assert result["status"] == "no_offers"
            assert airport.resolve_airport.await_count == 2
            provider.search_flights.assert_awaited_once()
            assert (
                provider.search_flights.await_args.kwargs["request"].return_date
                == return_date
            )

    asyncio.run(run())


def test_direct_service_cannot_bypass_capability_check():
    async def run():
        provider = AsyncMock()
        service = FlightSearchService(
            flight_provider=provider,
            clock=lambda: NOW,
            supports_round_trip=False,
        )
        with pytest.raises(UnsupportedFlightRequestError, match="one-way"):
            await service.search_flights(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="JFK",
                    departure_date=date(2027, 11, 7),
                    return_date=date(2027, 11, 10),
                )
            )
        provider.search_flights.assert_not_awaited()

    asyncio.run(run())
