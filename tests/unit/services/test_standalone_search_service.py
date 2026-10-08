"""Grounded standalone results, guidance, failures and cancellation."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.domain.flights import ONE_WAY_ONLY_MESSAGE
from app.mcp.schemas.currency import CurrencyConversionGuidance
from app.mcp.schemas.flights import FlightSearchGuidance
from app.mcp.schemas.hotels import HotelSearchGuidance
from app.mcp.schemas.places import PlaceSearchGuidance
from app.providers.currency.schemas import CurrencyConversionResult
from app.providers.flights.schemas import FlightSearchResult
from app.providers.weather.schemas import CurrentWeather
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationGuidance,
)
from app.services.planning_research_service import _compact_evidence
from app.services.standalone_search_service import (
    CurrencyRequest,
    FlightsRequest,
    HotelsRequest,
    PlacesRequest,
    StandaloneSearchService,
    WeatherRequest,
    guidance_reply,
)


def test_current_weather_and_conversion_render_only_provider_data():
    client = AsyncMock()
    client.get_current_weather.return_value = CurrentWeather(
        location="Lahore",
        condition="Clear",
        temperature_c=25,
        observed_at=datetime.now(UTC),
    )
    client.convert_currency.return_value = CurrencyConversionResult(
        amount=100,
        base_currency="USD",
        quote_currency="EUR",
        rate="0.9",
        converted_amount=90,
        rate_date="2026-10-01",
        observed_at=datetime.now(UTC),
    )
    service = StandaloneSearchService(
        client=client, enabled=frozenset({"weather", "currency"})
    )
    weather = asyncio.run(
        service.search(WeatherRequest(kind="weather", arguments={"city": "Lahore"}))
    )
    conversion = asyncio.run(
        service.search(
            CurrencyRequest(
                kind="currency",
                arguments={
                    "amount": 100,
                    "base_currency": "USD",
                    "quote_currency": "EUR",
                },
            )
        )
    )
    assert "25 °C" in weather.content and "not a forecast" in weather.content
    assert "90 EUR" in conversion.content and "2026-10-01" in conversion.content
    client.get_current_weather.assert_awaited_once_with(city="Lahore")


def test_hotel_request_requires_amount_for_specified_budget():
    with pytest.raises(ValueError, match="requires max_total_price"):
        HotelsRequest(
            kind="hotels",
            budget_decision="specified",
            arguments={
                "destination": "Lahore",
                "check_in_date": "2026-11-07",
                "check_out_date": "2026-11-10",
            },
        )


def test_hotel_no_limit_decision_allows_unfiltered_search():
    request = HotelsRequest(
        kind="hotels",
        budget_decision="no_limit",
        arguments={
            "destination": "Lahore",
            "check_in_date": "2026-11-07",
            "check_out_date": "2026-11-10",
        },
    )

    assert request.arguments.max_total_price is None


def test_hotel_specified_budget_preserves_total_and_currency():
    request = HotelsRequest(
        kind="hotels",
        budget_decision="specified",
        arguments={
            "destination": "Lahore",
            "check_in_date": "2026-11-07",
            "check_out_date": "2026-11-10",
            "currency": "PKR",
            "max_total_price": "200000",
        },
    )

    assert request.arguments.max_total_price == 200000
    assert request.arguments.currency == "PKR"


@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), ProviderUnavailableError("private"), asyncio.CancelledError()],
)
def test_search_failure_is_safe_and_cancellation_propagates(failure):
    client = AsyncMock()
    client.get_current_weather.side_effect = failure
    service = StandaloneSearchService(client=client, enabled=frozenset({"weather"}))
    request = WeatherRequest(kind="weather", arguments={"city": "Lahore"})
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(service.search(request))
    else:
        result = asyncio.run(service.search(request))
        assert "unavailable" in result.content and "private" not in result.content


@pytest.mark.parametrize(
    "search_request,method,result",
    [
        (
            PlacesRequest(kind="places", arguments={"destination": "Cambridge"}),
            "search_places",
            PlaceSearchGuidance(
                status="location_ambiguous",
                message="Choose a country",
                candidates=["Cambridge, UK", "Cambridge, USA"],
            ),
        ),
        (
            HotelsRequest(
                kind="hotels",
                arguments={
                    "destination": "Cambridge",
                    "check_in_date": "2099-11-07",
                    "check_out_date": "2099-11-09",
                },
            ),
            "search_hotels",
            HotelSearchGuidance(
                status="location_ambiguous",
                message="Choose a country",
                candidates=["Cambridge, UK", "Cambridge, USA"],
            ),
        ),
        (
            CurrencyRequest(
                kind="currency",
                arguments={
                    "amount": 100,
                    "base_currency": "USD",
                    "quote_currency": "EUR",
                },
            ),
            "convert_currency",
            CurrencyConversionGuidance(message="Reference pair unavailable"),
        ),
    ],
)
def test_guidance_preserves_candidates(search_request, method, result):
    client = AsyncMock()
    getattr(client, method).return_value = result
    service = StandaloneSearchService(
        client=client, enabled=frozenset({search_request.kind})
    )
    reply = asyncio.run(service.search(search_request))
    assert reply.input_required and result.message in reply.content
    for candidate in getattr(result, "candidates", []):
        assert candidate in reply.content
    getattr(client, method).assert_awaited_once_with(request=search_request.arguments)


def test_airport_guidance_retains_typed_client_requests():
    guidance = FlightSearchPreparationGuidance(
        origin={"status": "not_found", "query": "Unknown city"},
        destination={"status": "resolved", "query": "London", "iata_code": "LHR"},
        message="Clarify origin",
    )
    reply = guidance_reply(guidance)
    assert reply.input_required
    assert reply.clarification.requests[0].field == "origin_airport"
    assert reply.clarification.requests[0].query == "Unknown city"


def test_one_way_guidance_asks_the_user_without_exposing_tool_instructions():
    reply = guidance_reply(
        FlightSearchGuidance(status="unsupported_request", message=ONE_WAY_ONLY_MESSAGE)
    )
    assert "Would you like an outbound-only search?" in reply.content
    assert "Ask whether" not in reply.content
    assert reply.input_required


def flight_result():
    def leg(origin, destination, day):
        return {
            "duration_minutes": 60,
            "segments": [
                {
                    "departure_airport": origin,
                    "arrival_airport": destination,
                    "departure_at": f"2099-11-{day}T10:00:00Z",
                    "arrival_at": f"2099-11-{day}T11:00:00Z",
                    "departure_time_zone": "UTC",
                    "arrival_time_zone": "UTC",
                    "marketing_carrier_code": "BA",
                    "marketing_carrier_name": "British Airways",
                    "marketing_flight_number": "123",
                    "duration_minutes": 60,
                }
            ],
        }

    return FlightSearchResult(
        status="offers_available",
        searched_at=datetime.now(UTC),
        offers=[
            {
                "offer_id": "offer",
                "outbound": leg("LHR", "CDG", "07"),
                "return_itinerary": leg("CDG", "LHR", "09"),
                "total_price": 200,
                "currency": "USD",
                "traveler_count": 2,
                "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            }
        ],
    )


def test_return_leg_survives_research_and_standalone_rendering():
    result = flight_result()
    evidence = _compact_evidence(result, now=datetime.now(UTC))
    assert [item.id for item in evidence] == ["flight-1", "flight-1-return"]
    assert (
        evidence[1].starts_at
        == result.offers[0].return_itinerary.segments[0].departure_at
    )
    client = AsyncMock()
    client.search_flights.return_value = result
    service = StandaloneSearchService(client=client, enabled=frozenset({"flights"}))
    request = FlightsRequest(
        kind="flights",
        arguments={
            "origin": "LHR",
            "destination": "CDG",
            "departure_date": "2099-11-07",
            "return_date": "2099-11-09",
        },
    )
    reply = asyncio.run(service.search(request))
    assert "CDG → LHR" in reply.content and "2099-11-09" in reply.content
    result.offers[0].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert _compact_evidence(result, now=datetime.now(UTC)) == []
    assert "No current flight offers" in asyncio.run(service.search(request)).content


def test_disabled_capability_never_calls_provider():
    client = AsyncMock()
    service = StandaloneSearchService(client=client, enabled=frozenset())
    result = asyncio.run(
        service.search(WeatherRequest(kind="weather", arguments={"city": "Lahore"}))
    )
    assert "unavailable" in result.content
    client.get_current_weather.assert_not_awaited()
