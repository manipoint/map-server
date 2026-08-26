"""Tests for mapping normalized trips into provider search inputs."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.flights import FlightCabinClass
from app.domain.trips import TravelerParty, TripRequest
from app.services.trip_request_mapper import TripRequestMapper


def build_trip_request(**overrides: object) -> TripRequest:
    """Build one valid trip request with optional field overrides."""

    values: dict[str, object] = {
        "origin": "London",
        "destination": "New York",
        "start_date": date(2026, 9, 10),
        "end_date": date(2026, 9, 14),
    }
    values.update(overrides)
    return TripRequest.model_validate(values)


def test_to_flight_search_maps_complete_round_trip() -> None:
    """Flight mapping should preserve dates, party, and search preferences."""

    request = build_trip_request(
        travelers=TravelerParty(
            adults=2,
            children_ages=[7],
            infants_with_seat_ages=[1],
            infants_on_lap_ages=[0],
        ),
        total_budget=Decimal("5000"),
        budget_currency="gbp",
        cabin_class=FlightCabinClass.PREMIUM_ECONOMY,
        nonstop_only=True,
    )

    result = TripRequestMapper.to_flight_search(
        request,
        origin_airport_code="lhr",
        destination_airport_code="jfk",
    )

    assert result.origin == "LHR"
    assert result.destination == "JFK"
    assert result.departure_date == request.start_date
    assert result.return_date == request.end_date
    assert result.adults == 2
    assert result.children_ages == [7]
    assert result.infants_with_seat_ages == [1]
    assert result.infants_on_lap_ages == [0]
    assert result.cabin_class is FlightCabinClass.PREMIUM_ECONOMY
    assert result.nonstop_only is True
    assert result.currency == "GBP"
    assert result.max_results == 3


def test_to_flight_search_requires_trip_origin() -> None:
    """A local itinerary should not accidentally trigger a flight search."""

    request = build_trip_request(origin=None)

    with pytest.raises(
        ValueError,
        match="trip origin is required before creating a flight search",
    ):
        TripRequestMapper.to_flight_search(
            request,
            origin_airport_code="LHR",
            destination_airport_code="JFK",
        )


def test_to_hotel_search_maps_all_minor_travelers() -> None:
    """Hotels should receive every non-adult age without flight categories."""

    request = build_trip_request(
        travelers=TravelerParty(
            adults=2,
            children_ages=[5, 12],
            infants_with_seat_ages=[1],
            infants_on_lap_ages=[0],
        ),
        rooms=2,
        free_cancellation_only=True,
    )

    result = TripRequestMapper.to_hotel_search(request)

    assert result.destination == "New York"
    assert result.check_in_date == request.start_date
    assert result.check_out_date == request.end_date
    assert result.adults == 2
    assert result.children_ages == [5, 12, 1, 0]
    assert result.rooms == 2
    assert result.free_cancellation_only is True
    assert result.max_results == 3


@pytest.mark.parametrize(
    "trip_request",
    [
        build_trip_request(
            travelers=TravelerParty(adults=1, children_ages=[6]),
        ),
        build_trip_request(interests=["museums", "family activities"]),
    ],
)
def test_to_place_search_infers_family_friendly_request(
    trip_request: TripRequest,
) -> None:
    """Minor travelers or an explicit family interest should enable filtering."""

    result = TripRequestMapper.to_place_search(trip_request)

    assert result.family_friendly is True
    assert result.max_results == 3


def test_to_place_search_leaves_family_preference_unspecified() -> None:
    """An adults-only request should not invent a family preference."""

    request = build_trip_request(interests=["museums", "parks"])

    result = TripRequestMapper.to_place_search(request)

    assert result.destination == "New York"
    assert result.interests == ["museums", "parks"]
    assert result.family_friendly is None


def test_to_weather_search_uses_destination() -> None:
    """Weather mapping should use the normalized trip destination."""

    result = TripRequestMapper.to_weather_search(build_trip_request())

    assert result.city == "New York"


@pytest.mark.parametrize("max_results", [0, 6])
def test_to_place_search_delegates_result_limit_validation(
    max_results: int,
) -> None:
    """Provider schemas should reject mapper limits outside their contract."""

    with pytest.raises(ValidationError):
        TripRequestMapper.to_place_search(
            build_trip_request(),
            max_results=max_results,
        )
