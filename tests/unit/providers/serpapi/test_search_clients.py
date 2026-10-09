"""SerpApi flight and hotel search normalization without paid calls."""

import asyncio
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import ProviderError
from app.config import Settings
from app.providers.flights.local_metadata_provider import LocalFlightMetadataProvider
from app.providers.flights.metadata_schemas import (
    AirportMetadata,
    FlightMetadata,
)
from app.providers.flights.schemas import FlightSearchInput
from app.providers.hotels.schemas import HotelSearchInput, ResolvedHotelSearch
from app.providers.locations.schemas import ResolvedLocation
from app.providers.serpapi.client import SerpApiClient
from app.providers.serpapi.deals_client import FlightDeal, SerpApiDealsClient
from app.providers.serpapi.flight_client import SerpApiFlightClient
from app.providers.serpapi.hotel_client import SerpApiHotelClient


def settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
        "serpapi_api_key": SecretStr("test-serpapi-key"),
    }
    values.update(overrides)
    return Settings(**values)


def transport_for(payload, *, status=200, sort_by=None):
    def send(request):
        assert request.url.host == "serpapi.com"
        assert request.url.params["api_key"] == "test-serpapi-key"
        if sort_by is not None:
            assert request.url.params["sort_by"] == str(sort_by)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(send)


def flight_payload():
    def segment(origin, destination, departure, arrival, number, carrier):
        return {
            "departure_airport": {"id": origin, "time": departure},
            "arrival_airport": {"id": destination, "time": arrival},
            "duration": 120,
            "airline": carrier,
            "flight_number": number,
        }

    return {
        "search_metadata": {"status": "Success"},
        "google_flights_url": "https://www.google.com/travel/flights?sample=1",
        "best_flights": [
            {
                "price": 550,
                "booking_token": "opaque-token",
                "flights": [
                    segment(
                        "LHE",
                        "DXB",
                        "2030-03-01 10:00",
                        "2030-03-01 12:00",
                        "PK 203",
                        "Pakistan International Airlines",
                    ),
                    segment(
                        "DXB",
                        "LHR",
                        "2030-03-01 14:00",
                        "2030-03-01 18:00",
                        "EK 29",
                        "Emirates",
                    ),
                    segment(
                        "LHR",
                        "DXB",
                        "2030-03-08 10:00",
                        "2030-03-08 20:00",
                        "EK 30",
                        "Emirates",
                    ),
                    segment(
                        "DXB",
                        "LHE",
                        "2030-03-09 01:00",
                        "2030-03-09 05:00",
                        "PK 204",
                        "Pakistan International Airlines",
                    ),
                ],
            }
        ],
        "other_flights": [
            {
                "flights": [
                    segment(
                        "LHE",
                        "LHR",
                        "2030-03-01 10:00",
                        "2030-03-01 17:00",
                        "PK 205",
                        "Pakistan International Airlines",
                    ),
                    segment(
                        "LHR",
                        "LHE",
                        "2030-03-08 10:00",
                        "2030-03-08 20:00",
                        "PK 206",
                        "Pakistan International Airlines",
                    ),
                ]
            }
        ],
    }


def metadata_provider():
    zones = {
        "LHE": "Asia/Karachi",
        "DXB": "Asia/Dubai",
        "LHR": "Europe/London",
    }
    metadata = FlightMetadata(
        airports={
            code: AirportMetadata(iata_code=code, time_zone=zone)
            for code, zone in zones.items()
        }
    )
    return LocalFlightMetadataProvider(metadata=metadata)


def test_flights_return_all_priced_options_with_round_trip_segments():
    async def run():
        async with httpx.AsyncClient(
            transport=transport_for(flight_payload())
        ) as client:
            provider = SerpApiFlightClient(
                http_client=client,
                metadata_provider=metadata_provider(),
                settings=settings(),
            )
            return await provider.search_flights(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="LHR",
                    departure_date=date(2030, 3, 1),
                    return_date=date(2030, 3, 8),
                    currency="USD",
                )
            )

    result = asyncio.run(run())
    assert len(result.offers) == 2
    assert [item.total_price for item in result.offers] == [Decimal("550"), None]
    assert len(result.offers[0].outbound.segments) == 2
    assert len(result.offers[0].return_itinerary.segments) == 2
    assert result.offers[0].outbound.segments[0].departure_time_zone == "Asia/Karachi"
    assert result.offers[0].booking_url == (
        "https://www.google.com/travel/flights?sample=1"
    )


def test_malformed_return_option_does_not_discard_valid_sibling_options():
    payload = flight_payload()
    malformed = payload["best_flights"][0] | {
        "flights": payload["best_flights"][0]["flights"][:2]
    }
    payload["other_flights"].append(malformed)

    async def run():
        async with httpx.AsyncClient(transport=transport_for(payload)) as client:
            provider = SerpApiFlightClient(
                http_client=client,
                metadata_provider=metadata_provider(),
                settings=settings(),
            )
            return await provider.search_flights(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="LHR",
                    departure_date=date(2030, 3, 1),
                    return_date=date(2030, 3, 8),
                    currency="USD",
                )
            )

    result = asyncio.run(run())
    assert len(result.offers) == 2
    assert result.message is None


def test_only_malformed_return_options_yield_empty_search_result():
    payload = flight_payload()
    payload["best_flights"] = [
        payload["best_flights"][0]
        | {"flights": payload["best_flights"][0]["flights"][:2]}
    ]
    payload["other_flights"] = []

    async def run():
        async with httpx.AsyncClient(transport=transport_for(payload)) as client:
            provider = SerpApiFlightClient(
                http_client=client,
                metadata_provider=metadata_provider(),
                settings=settings(),
            )
            return await provider.search_flights(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="LHR",
                    departure_date=date(2030, 3, 1),
                    return_date=date(2030, 3, 8),
                    currency="USD",
                )
            )

    result = asyncio.run(run())
    assert result.offers == []
    assert result.message == "Flight options were incomplete. Try the search again."


def test_hotels_return_distinct_options_ordered_by_rating_then_price():
    payload = {
        "search_metadata": {"status": "Success"},
        "properties": [
            {
                "name": "Hotel One",
                "property_token": "hotel-1",
                "link": "https://hotel.example/book",
                "gps_coordinates": {"latitude": 24.8, "longitude": 67.0},
                "overall_rating": 4.5,
                "reviews": 50,
                "prices": [
                    {
                        "source": "Budget Booking",
                        "link": "https://booking.example/one",
                        "rate_per_night": {"extracted_lowest": 50},
                    },
                    {
                        "source": "Premium Booking",
                        "rate_per_night": {"extracted_lowest": 70},
                    },
                ],
            },
            {"name": "Hotel Two", "property_token": "hotel-2"},
        ],
        "ads": [
            {
                "name": "Hotel Three",
                "property_token": "hotel-3",
                "source": "Other Booking",
                "link": "https://booking.example/three",
                "extracted_price": 40,
            }
        ],
    }

    async def run():
        async with httpx.AsyncClient(
            transport=transport_for(payload, sort_by=8)
        ) as client:
            provider = SerpApiHotelClient(http_client=client, settings=settings())
            request = HotelSearchInput(
                destination="Karachi",
                check_in_date=date(2030, 3, 1),
                check_out_date=date(2030, 3, 3),
                currency="USD",
            )
            return await provider.search_hotels(
                search=ResolvedHotelSearch(
                    request=request,
                    location=ResolvedLocation(
                        query="Karachi",
                        display_name="Karachi, Pakistan",
                        latitude=24.8,
                        longitude=67.0,
                    ),
                    radius_km=25,
                )
            )

    result = asyncio.run(run())
    assert len(result.options) == 3
    assert [option.hotel.name for option in result.options] == [
        "Hotel One",
        "Hotel Three",
        "Hotel Two",
    ]
    assert [option.cheapest_total_price for option in result.options] == [
        Decimal("100"),
        Decimal("80"),
        None,
    ]
    assert result.options[0].provider_source == "Budget Booking"
    assert str(result.options[0].booking_url) == "https://booking.example/one"
    assert result.options[-1].hotel.name == "Hotel Two"


def test_hotel_budget_keeps_only_known_prices_within_total_stay_limit():
    payload = {
        "search_metadata": {"status": "Success"},
        "properties": [
            {
                "name": "Over Budget",
                "property_token": "hotel-high",
                "overall_rating": 4.9,
                "total_rate": {"extracted_lowest": 950},
            },
            {
                "name": "Within Budget",
                "property_token": "hotel-low",
                "overall_rating": 4.5,
                "total_rate": {"extracted_lowest": 850},
            },
            {
                "name": "Unknown Price",
                "property_token": "hotel-unknown",
                "overall_rating": 5.0,
            },
        ],
    }

    async def run():
        async with httpx.AsyncClient(
            transport=transport_for(payload, sort_by=3)
        ) as client:
            provider = SerpApiHotelClient(http_client=client, settings=settings())
            request = HotelSearchInput(
                destination="Karachi",
                check_in_date=date(2030, 3, 1),
                check_out_date=date(2030, 3, 3),
                currency="USD",
                max_total_price=900,
            )
            return await provider.search_hotels(
                search=ResolvedHotelSearch(
                    request=request,
                    location=ResolvedLocation(
                        query="Karachi",
                        display_name="Karachi, Pakistan",
                        latitude=24.8,
                        longitude=67.0,
                    ),
                    radius_km=25,
                )
            )

    result = asyncio.run(run())

    assert [option.hotel.name for option in result.options] == ["Within Budget"]
    assert result.options[0].cheapest_total_price == Decimal("850")


def test_hotel_budget_with_no_matches_returns_clear_message():
    payload = {
        "search_metadata": {"status": "Success"},
        "properties": [
            {
                "name": "Over Budget",
                "property_token": "hotel-high",
                "total_rate": {"extracted_lowest": 950},
            }
        ],
    }

    async def run():
        async with httpx.AsyncClient(transport=transport_for(payload)) as client:
            provider = SerpApiHotelClient(http_client=client, settings=settings())
            request = HotelSearchInput(
                destination="Karachi",
                check_in_date=date(2030, 3, 1),
                check_out_date=date(2030, 3, 3),
                max_total_price=900,
            )
            return await provider.search_hotels(
                search=ResolvedHotelSearch(
                    request=request,
                    location=ResolvedLocation(
                        query="Karachi",
                        display_name="Karachi, Pakistan",
                        latitude=24.8,
                        longitude=67.0,
                    ),
                    radius_km=25,
                )
            )

    result = asyncio.run(run())

    assert result.status.value == "no_hotels"
    assert result.options == []
    assert "within the stated total-stay budget" in result.message


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"search_metadata": {"status": "Error"}}, 200),
        ({"search_metadata": {"status": "Success"}}, 401),
        ({"search_metadata": {"status": "Success"}}, 503),
    ],
)
def test_serpapi_errors_are_sanitized(payload, status):
    async def run():
        async with httpx.AsyncClient(
            transport=transport_for(payload, status=status)
        ) as client:
            return await SerpApiClient(http_client=client, settings=settings()).search(
                engine="google_flights", params={}
            )

    with pytest.raises(ProviderError, match="SerpApi"):
        asyncio.run(run())


def test_flight_results_are_bounded_by_configured_maximum():
    payload = flight_payload()
    payload["other_flights"] *= 100

    async def run():
        async with httpx.AsyncClient(transport=transport_for(payload)) as client:
            provider = SerpApiFlightClient(
                http_client=client,
                metadata_provider=metadata_provider(),
                settings=settings(max_search_results=2),
            )
            return await provider.search_flights(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="LHR",
                    departure_date=date(2030, 3, 1),
                    return_date=date(2030, 3, 8),
                    max_results=100,
                )
            )

    assert len(asyncio.run(run()).offers) == 2


def test_flight_deal_duration_uses_inclusive_trip_dates():
    deal = FlightDeal(
        origin="LHE",
        destination="DXB",
        start_date=date(2030, 12, 1),
        end_date=date(2030, 12, 5),
        price=Decimal("200"),
        currency="USD",
        flight_link="https://www.google.com/travel/flights?sample=1",
    )

    assert deal.duration_days == 5


def test_deals_search_filters_route_and_window_then_sorts_all_matches():
    payload = {
        "search_metadata": {"status": "Success"},
        "deals": [
            {
                "departure_airport_code": "LHE",
                "arrival_airport_code": "DXB",
                "start_date": "2030-12-02",
                "end_date": "2030-12-08",
                "price": 300,
                "airline": "Airline A",
                "stops": 1,
                "flight_link": "https://www.google.com/travel/flights?deal=expensive",
            },
            {
                "departure_airport_code": "LHE",
                "arrival_airport_code": "DXB",
                "start_date": "2030-12-10",
                "end_date": "2030-12-14",
                "price": 200,
                "airline": "Airline B",
                "stops": 0,
                "flight_link": "https://www.google.com/travel/flights?deal=cheap",
            },
            {
                "departure_airport_code": "LHE",
                "arrival_airport_code": "LHR",
                "start_date": "2030-12-03",
                "end_date": "2030-12-07",
                "price": 100,
                "flight_link": "https://www.google.com/travel/flights?deal=wrong-route",
            },
            {
                "departure_airport_code": "LHE",
                "arrival_airport_code": "DXB",
                "start_date": "2030-12-30",
                "end_date": "2031-01-04",
                "price": 150,
                "flight_link": "https://www.google.com/travel/flights?deal=outside",
            },
            {
                "departure_airport_code": "LHE",
                "arrival_airport_code": "DXB",
                "start_date": "2030-12-15",
                "end_date": "2030-12-19",
                "price": 120,
                "flight_link": "https://example.com/travel/flights?deal=invalid-link",
            },
        ],
    }
    requests = []

    def send(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            provider = SerpApiDealsClient(http_client=client, settings=settings())
            return await provider.search_deals(
                origin_code="LHE",
                destination_codes={"DXB"},
                window_start=date(2030, 12, 1),
                window_end=date(2030, 12, 31),
                currency="USD",
                adults=2,
            )

    deals = asyncio.run(run())

    assert len(requests) == 1
    assert requests[0].url.params["engine"] == "google_flights_deals"
    assert requests[0].url.params["outbound_date"] == "2030-12-01,2030-12-31"
    assert requests[0].url.params["adults"] == "2"
    assert "trip_length" not in requests[0].url.params
    assert [deal.price for deal in deals] == [Decimal("200"), Decimal("300")]
    assert [deal.duration_days for deal in deals] == [5, 7]


def test_deals_search_returns_empty_when_provider_has_no_matching_destination():
    payload = {
        "search_metadata": {"status": "Success"},
        "deals": [
            {
                "departure_airport_code": "LHE",
                "arrival_airport_code": "LHR",
                "start_date": "2030-12-02",
                "end_date": "2030-12-08",
                "price": 300,
                "flight_link": "https://www.google.com/travel/flights?deal=london",
            }
        ],
    }

    async def run():
        async with httpx.AsyncClient(transport=transport_for(payload)) as client:
            provider = SerpApiDealsClient(http_client=client, settings=settings())
            return await provider.search_deals(
                origin_code="LHE",
                destination_codes={"DXB"},
                window_start=date(2030, 12, 1),
                window_end=date(2030, 12, 31),
                currency="USD",
                adults=1,
            )

    assert asyncio.run(run()) == []


def test_deals_search_skips_invalid_prices_and_wrong_origin_without_losing_valid_deal():
    payload = {
        "search_metadata": {"status": "Success"},
        "deals": [
            {
                "departure_airport_code": "ISB",
                "arrival_airport_code": "DXB",
                "start_date": "2030-12-02",
                "end_date": "2030-12-08",
                "price": 100,
                "flight_link": "https://www.google.com/travel/flights?deal=wrong-origin",
            },
            {
                "departure_airport_code": "LHE",
                "arrival_airport_code": "DXB",
                "start_date": "2030-12-10",
                "end_date": "2030-12-14",
                "price": 200,
                "flight_link": "https://www.google.com/travel/flights?deal=valid",
            },
            {
                "departure_airport_code": "LHE",
                "arrival_airport_code": "DXB",
                "start_date": "2030-12-16",
                "end_date": "2030-12-20",
                "price": "invalid-price",
                "flight_link": "https://www.google.com/travel/flights?deal=bad-price",
            },
        ],
    }

    async def run():
        async with httpx.AsyncClient(transport=transport_for(payload)) as client:
            provider = SerpApiDealsClient(http_client=client, settings=settings())
            return await provider.search_deals(
                origin_code="LHE",
                destination_codes={"DXB"},
                window_start=date(2030, 12, 1),
                window_end=date(2030, 12, 31),
                currency="USD",
                adults=1,
            )

    deals = asyncio.run(run())

    assert [deal.price for deal in deals] == [Decimal("200")]
    assert deals[0].origin == "LHE"


def test_deals_search_rejects_invalid_party_before_provider_call():
    requests = []

    def send(request):
        requests.append(request)
        return httpx.Response(200, json={"search_metadata": {"status": "Success"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            provider = SerpApiDealsClient(http_client=client, settings=settings())
            return await provider.search_deals(
                origin_code="LHE",
                destination_codes={"DXB"},
                window_start=date(2030, 12, 1),
                window_end=date(2030, 12, 31),
                currency="USD",
                adults=0,
            )

    with pytest.raises(ValueError):
        asyncio.run(run())
    assert requests == []
