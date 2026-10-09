"""Deal discovery uses local airport resolution before one paid search."""

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.providers.airports.schemas import AirportOption, AirportResolution
from app.providers.serpapi.deals_client import FlightDeal
from app.services.deal_discovery_service import (
    DealDiscoveryInput,
    DealDiscoveryService,
)


def request(**changes: object) -> DealDiscoveryInput:
    values: dict[str, object] = {
        "origin": "Lahore",
        "destination": "Dubai",
        "window_start": date(2099, 12, 1),
        "window_end": date(2099, 12, 31),
        "adults": 2,
    }
    values.update(changes)
    return DealDiscoveryInput.model_validate(values)


def resolved(query: str, code: str) -> AirportResolution:
    return AirportResolution(status="resolved", query=query, iata_code=code)


def deal(*, start_day: int, end_day: int, price: str) -> FlightDeal:
    return FlightDeal(
        origin="LHE",
        destination="DXB",
        start_date=date(2099, 12, start_day),
        end_date=date(2099, 12, end_day),
        price=Decimal(price),
        currency="USD",
        flight_link=f"https://www.google.com/travel/flights?start={start_day}",
    )


def test_resolved_route_searches_deals_once_and_preserves_mixed_durations():
    airports = AsyncMock()
    airports.resolve_airport.side_effect = lambda *, request: resolved(
        request.query, {"Lahore": "LHE", "Dubai": "DXB"}[request.query]
    )
    deals_client = AsyncMock()
    deals_client.search_deals.return_value = [
        deal(start_day=2, end_day=8, price="300"),
        deal(start_day=10, end_day=14, price="200"),
    ]
    service = DealDiscoveryService(
        airport_resolution_service=airports,
        deals_client=deals_client,
    )

    result = asyncio.run(service.discover(request=request()))

    assert airports.resolve_airport.await_count == 2
    deals_client.search_deals.assert_awaited_once_with(
        origin_code="LHE",
        destination_codes={"DXB"},
        window_start=date(2099, 12, 1),
        window_end=date(2099, 12, 31),
        currency="USD",
        adults=2,
        children=0,
        infants_in_seat=0,
        infants_in_lap=0,
    )
    assert result.status == "deals_available"
    assert [choice.deal.duration_days for choice in result.options] == [7, 5]
    assert len({choice.option_id for choice in result.options}) == 2
    assert all(len(choice.option_id) == 32 for choice in result.options)


def test_ambiguous_airport_returns_choices_without_paid_search():
    airport_option = AirportOption(
        provider_location_id="lahore-1",
        iata_code="LHE",
        location_type="airport",
        name="Allama Iqbal International Airport",
        country_code="PK",
    )
    other_option = airport_option.model_copy(
        update={
            "provider_location_id": "lahore-2",
            "iata_code": "XYZ",
            "name": "Other Lahore Airport",
        }
    )
    ambiguous = AirportResolution(
        status="selection_required",
        query="Lahore",
        options=[airport_option, other_option],
    )
    airports = AsyncMock()
    airports.resolve_airport.side_effect = lambda *, request: (
        ambiguous if request.query == "Lahore" else resolved("Dubai", "DXB")
    )
    deals_client = AsyncMock()
    service = DealDiscoveryService(
        airport_resolution_service=airports,
        deals_client=deals_client,
    )

    result = asyncio.run(service.discover(request=request()))

    assert airports.resolve_airport.await_count == 2
    assert result.status == "airport_resolution_required"
    assert result.origin.status == "selection_required"
    assert result.options == ()
    deals_client.search_deals.assert_not_awaited()


def test_resolved_route_with_no_returned_deals_is_not_a_no_flights_claim():
    airports = AsyncMock()
    airports.resolve_airport.side_effect = lambda *, request: resolved(
        request.query, {"Lahore": "LHE", "Dubai": "DXB"}[request.query]
    )
    deals_client = AsyncMock()
    deals_client.search_deals.return_value = []
    service = DealDiscoveryService(
        airport_resolution_service=airports,
        deals_client=deals_client,
    )

    result = asyncio.run(service.discover(request=request()))

    assert result.status == "no_deals"
    assert result.options == ()
    deals_client.search_deals.assert_awaited_once()


@pytest.mark.parametrize(
    "changes",
    [
        {"window_end": date(2099, 11, 30)},
        {"window_end": date(2100, 1, 1)},
        {"adults": 0},
        {"adults": 8, "children": 2},
        {"adults": 1, "infants_on_lap": 2},
    ],
)
def test_invalid_deal_request_is_rejected_before_search(changes: dict[str, object]):
    with pytest.raises(ValidationError):
        request(**changes)
