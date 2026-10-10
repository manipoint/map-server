"""Grounded standalone results, guidance, failures and cancellation."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.domain.flights import ONE_WAY_ONLY_MESSAGE
from app.mcp.schemas.currency import CurrencyConversionGuidance
from app.mcp.schemas.flights import FlightSearchGuidance
from app.mcp.schemas.hotels import HotelSearchGuidance
from app.mcp.schemas.places import PlaceSearchGuidance
from app.providers.airports.schemas import AirportOption, AirportResolution
from app.providers.currency.schemas import CurrencyConversionResult
from app.providers.flights.schemas import FlightSearchResult
from app.providers.serpapi.deals_client import FlightDeal
from app.providers.weather.schemas import CurrentWeather
from app.services.deal_discovery_service import DealChoice, DealDiscoveryResult
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationGuidance,
)
from app.services.planning_research_service import _compact_evidence
from app.services.standalone_search_service import (
    CurrencyRequest,
    FlightDealsRequest,
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


def _flight_deals_request() -> FlightDealsRequest:
    return FlightDealsRequest(
        kind="flight_deals",
        arguments={
            "origin": "Lahore",
            "destination": "Dubai",
            "window_start": date(2099, 12, 1),
            "window_end": date(2099, 12, 31),
        },
    )


def _deal_result(status: str = "deals_available") -> DealDiscoveryResult:
    return DealDiscoveryResult(
        status=status,
        origin=AirportResolution(status="resolved", query="Lahore", iata_code="LHE"),
        destination=AirportResolution(
            status="resolved", query="Dubai", iata_code="DXB"
        ),
        options=(
            tuple(
                DealChoice(
                    option_id=f"option-{index}",
                    deal=FlightDeal(
                        origin="LHE",
                        destination="DXB",
                        start_date=date(2099, 12, index),
                        end_date=date(2099, 12, index + 4),
                        price=Decimal(200 + index),
                        currency="USD",
                        airline="Example Air",
                        stops=0,
                        flight_link="https://www.google.com/travel/flights",
                    ),
                )
                for index in (2, 10)
            )
            if status == "deals_available"
            else ()
        ),
    )


def test_standalone_deals_default_party_and_render_every_option():
    request = _flight_deals_request()
    assert request.arguments.adults == 1
    client = AsyncMock()
    deals = SimpleNamespace(discover=AsyncMock())
    deals.discover.return_value = _deal_result()
    service = StandaloneSearchService(
        client=client,
        enabled=frozenset({"flight_deals"}),
        deal_discovery_service=deals,
    )

    reply = asyncio.run(service.search(request))

    assert "1 adult" in reply.content
    assert "2099-12-02" in reply.content
    assert "2099-12-10" in reply.content
    assert "Nothing is booked" in reply.content
    deals.discover.assert_awaited_once_with(request=request.arguments)
    client.search_flights.assert_not_awaited()


def test_standalone_deals_empty_feed_is_not_reported_as_no_flights():
    deals = SimpleNamespace(discover=AsyncMock())
    deals.discover.return_value = _deal_result("no_deals")
    service = StandaloneSearchService(
        client=AsyncMock(),
        enabled=frozenset({"flight_deals"}),
        deal_discovery_service=deals,
    )

    reply = asyncio.run(service.search(_flight_deals_request()))

    assert "No matching deals" in reply.content
    assert "does not mean there are no flights" in reply.content
    assert reply.deal_result is not None
    assert reply.deal_result.status == "no_deals"


def test_standalone_deals_ambiguous_airport_returns_typed_clarification():
    option = AirportOption(
        provider_location_id="LHE",
        iata_code="LHE",
        location_type="airport",
        name="Lahore Airport",
        country_code="PK",
    )
    deals = SimpleNamespace(discover=AsyncMock())
    deals.discover.return_value = _deal_result(
        "airport_resolution_required"
    ).model_copy(
        update={
            "origin": AirportResolution(
                status="selection_required",
                query="Lahore",
                options=[
                    option,
                    option.model_copy(
                        update={
                            "provider_location_id": "LHR",
                            "iata_code": "LHR",
                            "name": "Other Airport",
                        }
                    ),
                ],
            )
        }
    )
    service = StandaloneSearchService(
        client=AsyncMock(),
        enabled=frozenset({"flight_deals"}),
        deal_discovery_service=deals,
    )

    reply = asyncio.run(service.search(_flight_deals_request()))

    assert reply.input_required is True
    assert reply.clarification is not None
    assert "Lahore" in reply.content
    assert any(
        option.iata_code == "LHE" for option in reply.clarification.requests[0].options
    )


def test_disabled_standalone_deals_never_calls_provider():
    deals = SimpleNamespace(discover=AsyncMock())
    service = StandaloneSearchService(
        client=AsyncMock(),
        enabled=frozenset(),
        deal_discovery_service=deals,
    )
    reply = asyncio.run(service.search(_flight_deals_request()))
    assert "unavailable" in reply.content
    deals.discover.assert_not_awaited()
