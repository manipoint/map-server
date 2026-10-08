"""Research tool isolation, concurrency and provider degradation."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.domain.flights import FlightSearchStatus
from app.domain.trip_requirements import TripRequirements
from app.providers.flights.schemas import (
    FlightItinerary,
    FlightOffer,
    FlightSearchResult,
    FlightSegment,
)
from app.providers.places.schemas import PlaceSearchResult
from app.providers.weather.schemas import (
    DailyWeatherForecast,
    HourlyWeatherForecast,
    WeatherForecast,
)
from app.services.planning_research_service import (
    PlanningResearchService,
    ResearchEvidence,
)


def complete_requirements(**changes):
    return TripRequirements.model_validate(
        {
            "destination": "Japan",
            "origin": "Lahore",
            "start_date": "2099-11-07",
            "duration_days": 5,
            "adults": 2,
            "minor_count": 0,
            "transport": "road",
            "needs_lodging": False,
            "budget_decision": "undecided",
            **changes,
        }
    )


def create_service(client, **changes):
    return PlanningResearchService(
        client=client,
        **{
            "places_available": True,
            "hotels_available": False,
            "round_trip_flights_available": False,
            "weather_forecasts_available": False,
            **changes,
        },
    )


def place_result():
    return PlaceSearchResult.model_validate(
        {
            "status": "places_available",
            "searched_at": datetime.now(UTC),
            "location": {
                "query": "Japan",
                "display_name": "Japan",
                "latitude": 35,
                "longitude": 139,
            },
            "places": [
                {
                    "provider_place_id": "tokyo-1",
                    "name": "Temple",
                    "summary": "A temple visit",
                    "source_urls": ["https://example.com/place"],
                }
            ]
            * 2,
        }
    )


def flight_result(offer_id: str, departure_date: date) -> FlightSearchResult:
    outbound_departure = datetime(
        departure_date.year,
        departure_date.month,
        departure_date.day,
        8,
        tzinfo=ZoneInfo("Asia/Karachi"),
    )
    outbound_arrival = outbound_departure.replace(hour=14)
    return_departure = outbound_departure + timedelta(days=4)
    return_arrival = return_departure.replace(hour=20)

    def segment(origin: str, destination: str, departure: datetime, arrival: datetime):
        return FlightSegment(
            departure_airport=origin,
            arrival_airport=destination,
            departure_at=departure,
            arrival_at=arrival,
            departure_time_zone="Asia/Karachi",
            arrival_time_zone="Europe/Istanbul",
            marketing_carrier_code="TK",
            marketing_carrier_name="Turkish Airlines",
            marketing_flight_number="123",
            duration_minutes=360,
        )

    return FlightSearchResult(
        status=FlightSearchStatus.OFFERS_AVAILABLE,
        searched_at=datetime.now(UTC),
        offers=[
            FlightOffer(
                offer_id=offer_id,
                outbound=FlightItinerary(
                    segments=[
                        segment("LHE", "IST", outbound_departure, outbound_arrival)
                    ],
                    duration_minutes=360,
                ),
                return_itinerary=FlightItinerary(
                    segments=[segment("IST", "LHE", return_departure, return_arrival)],
                    duration_minutes=360,
                ),
                total_price="500",
                currency="USD",
                traveler_count=2,
            )
        ],
    )


def test_research_compacts_and_deduplicates_verified_places():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    result = asyncio.run(create_service(client).research(complete_requirements()))
    assert len(result.evidence) == 1
    assert result.evidence[0].id == "place-1"
    client.search_hotels.assert_not_awaited()
    client.search_flights.assert_not_awaited()
    client.get_weather_forecast.assert_not_awaited()


def test_unavailable_roundtrip_and_hotel_providers_are_not_called():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    result = asyncio.run(
        create_service(client).research(
            complete_requirements(
                transport="flight", cabin_class="economy", needs_lodging=True, rooms=1
            )
        )
    )
    assert len(result.warnings) == 2
    assert any("flights" in warning for warning in result.warnings)
    client.search_hotels.assert_not_awaited()
    client.search_flights.assert_not_awaited()


def test_flight_search_checks_adjacent_round_trip_dates_only_after_no_exact_offers():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    client.search_flights.side_effect = [
        FlightSearchResult(
            status=FlightSearchStatus.NO_OFFERS,
            searched_at=datetime.now(UTC),
            message="No options on requested dates.",
        ),
        flight_result("day-before", date(2099, 11, 6)),
        flight_result("day-after", date(2099, 11, 8)),
    ]
    result = asyncio.run(
        create_service(client, round_trip_flights_available=True).research(
            complete_requirements(
                transport="flight",
                cabin_class="economy",
                start_date="2099-11-07",
                end_date="2099-11-11",
            )
        )
    )

    assert client.search_flights.await_count == 3
    requests = [
        call.kwargs["request"] for call in client.search_flights.await_args_list
    ]
    assert [(request.departure_date, request.return_date) for request in requests] == [
        (date(2099, 11, 7), date(2099, 11, 11)),
        (date(2099, 11, 6), date(2099, 11, 10)),
        (date(2099, 11, 8), date(2099, 11, 12)),
    ]
    alternatives = [
        evidence for evidence in result.evidence if evidence.kind == "flight"
    ]
    assert {item.alternative_start_date for item in alternatives} == {
        date(2099, 11, 6),
        date(2099, 11, 8),
    }
    assert {item.alternative_end_date for item in alternatives} == {
        date(2099, 11, 10),
        date(2099, 11, 12),
    }


def test_exact_flight_offers_do_not_trigger_adjacent_date_searches():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    client.search_flights.return_value = flight_result("exact", date(2099, 11, 7))
    result = asyncio.run(
        create_service(client, round_trip_flights_available=True).research(
            complete_requirements(
                transport="flight",
                cabin_class="economy",
                start_date="2099-11-07",
                end_date="2099-11-11",
            )
        )
    )
    client.search_flights.assert_awaited_once()
    flights = [item for item in result.evidence if item.kind == "flight"]
    assert len(flights) == 2
    assert all(item.alternative_start_date is None for item in flights)


def test_flight_provider_failure_does_not_trigger_paid_nearby_searches():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    client.search_flights.side_effect = ProviderUnavailableError("provider unavailable")
    result = asyncio.run(
        create_service(client, round_trip_flights_available=True).research(
            complete_requirements(
                transport="flight",
                cabin_class="economy",
                start_date="2099-11-07",
                end_date="2099-11-11",
            )
        )
    )
    client.search_flights.assert_awaited_once()
    assert any("search failed" in warning for warning in result.warnings)
    assert not any(item.kind == "flight" for item in result.evidence)


def test_nearby_date_options_are_not_repeated_after_user_selected_one():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    client.search_flights.return_value = FlightSearchResult(
        status=FlightSearchStatus.NO_OFFERS,
        searched_at=datetime.now(UTC),
        message="No options on those dates.",
    )
    result = asyncio.run(
        create_service(client, round_trip_flights_available=True).research(
            complete_requirements(
                transport="flight",
                cabin_class="economy",
                start_date="2099-11-08",
                end_date="2099-11-12",
            ),
            allow_nearby_flight_dates=False,
        )
    )
    client.search_flights.assert_awaited_once()
    assert "already checked" in result.warnings[0]


@pytest.mark.parametrize(
    "failure", [TimeoutError(), ProviderUnavailableError("private provider error")]
)
def test_expected_failure_is_safe_and_does_not_prevent_draft(failure):
    client = AsyncMock()
    client.search_places.side_effect = failure
    result = asyncio.run(create_service(client).research(complete_requirements()))
    assert not result.evidence
    assert len(result.warnings) == 1
    assert "private" not in str(result)


def test_research_cancellation_propagates():
    client = AsyncMock()
    client.search_places.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(create_service(client).research(complete_requirements()))


def test_itinerary_research_keeps_partial_weather_and_resolves_schedule_timezone():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    client.get_weather_forecast.return_value = WeatherForecast(
        location="Tokyo",
        country="Japan",
        time_zone="Asia/Tokyo",
        days=(
            DailyWeatherForecast(
                date=date(2099, 11, 7),
                condition="Rain",
                max_temperature_c=18,
                min_temperature_c=11,
                total_precipitation_mm=3,
                total_snow_cm=0,
                hours=(
                    HourlyWeatherForecast(
                        local_time=datetime(
                            2099, 11, 7, 14, tzinfo=ZoneInfo("Asia/Tokyo")
                        ),
                        condition="Rain",
                        temperature_c=16,
                        chance_of_rain_percent=90,
                        chance_of_snow_percent=0,
                        precipitation_mm=2,
                        snow_cm=0,
                    ),
                ),
            ),
        ),
    )

    result = asyncio.run(
        create_service(client, weather_forecasts_available=True).research(
            complete_requirements(
                destination="Tokyo",
                start_date="2099-11-07",
                end_date="2099-11-08",
                duration_days=2,
            )
        )
    )

    assert result.weather_forecast is not None
    assert result.weather_forecast.days[0].hours[0].chance_of_rain_percent == 90
    client.get_weather_forecast.assert_awaited_once_with(
        city="Tokyo",
        start_date=date(2099, 11, 7),
        end_date=date(2099, 11, 8),
    )
    assert result.weather_requested
    assert result.time_zone == "Asia/Tokyo"
    assert result.warnings == ()  # Normal coverage gaps do not disable reuse.


def test_independent_searches_start_concurrently():
    async def scenario():
        both_started = asyncio.Event()
        started = []

        async def search(*, request):
            started.append(request)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return place_result()

        client = AsyncMock()
        client.search_places.side_effect = search
        client.search_hotels.side_effect = search
        result = await create_service(client, hotels_available=True).research(
            complete_requirements(needs_lodging=True, rooms=1)
        )
        assert len(started) == 2
        return result

    asyncio.run(scenario())


def test_weather_timezone_is_used_when_flight_does_not_supply_one():
    client = AsyncMock()
    client.get_weather_forecast.return_value = WeatherForecast(
        location="Tokyo",
        time_zone="Asia/Tokyo",
        days=(),
    )
    service = create_service(client, weather_forecasts_available=True)
    service._search = AsyncMock(
        return_value=(
            [
                ResearchEvidence(
                    id="flight-1",
                    kind="flight",
                    name="Flight",
                    location="Tokyo",
                    description="No verified timezone",
                ),
            ],
            None,
            None,
        )
    )
    result = asyncio.run(service.research(complete_requirements()))
    assert result.time_zone == "Asia/Tokyo"
    assert result.weather_forecast.days == ()
