"""Tests for deterministic airport preparation before flight search."""

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.domain.flights import FlightSearchStatus
from app.providers.airports.schemas import AirportOption, AirportResolution
from app.providers.flights.schemas import FlightSearchResult
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationGuidance,
    FlightSearchPreparationInput,
    FlightSearchPreparationService,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def request(**overrides: object) -> FlightSearchPreparationInput:
    """Create one preparation request containing location names."""

    values: dict[str, object] = {
        "origin": " Lahore ",
        "destination": " London, United Kingdom ",
        "departure_date": date(2026, 9, 10),
        "return_date": date(2026, 9, 17),
        "adults": 1,
        "children_ages": [8],
        "currency": "pkr",
        "max_results": 3,
    }
    values.update(overrides)
    return FlightSearchPreparationInput.model_validate(values)


def resolved(query: str, code: str) -> AirportResolution:
    """Create one resolved airport outcome."""

    return AirportResolution(status="resolved", query=query, iata_code=code)


def option(code: str, name: str) -> AirportOption:
    """Create one airport selection option."""

    return AirportOption(
        provider_location_id=f"apt_{code.lower()}",
        iata_code=code,
        location_type="airport",
        name=name,
        city_name="London",
        country_name="United Kingdom",
        country_code="GB",
    )


def no_offers() -> FlightSearchResult:
    """Create one deterministic flight result."""

    return FlightSearchResult(
        status=FlightSearchStatus.NO_OFFERS,
        searched_at=NOW,
        message="No current offers were found.",
    )


def create_service(
    *,
    origin: AirportResolution,
    destination: AirportResolution,
) -> tuple[FlightSearchPreparationService, AsyncMock, MagicMock]:
    """Create deterministic airport and flight service doubles."""

    airport_service = AsyncMock()
    airport_service.resolve_airport.side_effect = [origin, destination]
    flight_service = MagicMock()
    flight_service.evaluate_request_policy.return_value = None
    flight_service.search_flights = AsyncMock()
    flight_service.search_flights.return_value = no_offers()
    service = FlightSearchPreparationService(
        airport_resolution_service=airport_service,
        flight_search_service=flight_service,
    )
    return service, airport_service, flight_service


def test_preparation_resolves_both_locations_then_searches_once() -> None:
    """An unambiguous route should become one normalized provider request."""

    service, airport_service, flight_service = create_service(
        origin=resolved("Lahore", "LHE"),
        destination=resolved("London, United Kingdom", "LHR"),
    )

    result = asyncio.run(service.prepare_and_search(request=request()))

    assert result is flight_service.search_flights.return_value
    flight_service.evaluate_request_policy.assert_called_once()
    assert airport_service.resolve_airport.await_count == 2
    queries = {
        call.kwargs["request"].query
        for call in airport_service.resolve_airport.await_args_list
    }
    assert queries == {"Lahore", "London, United Kingdom"}
    flight_request = flight_service.search_flights.await_args.kwargs["request"]
    assert flight_request.origin == "LHE"
    assert flight_request.destination == "LHR"
    assert flight_request.children_ages == [8]
    assert flight_request.currency == "PKR"
    assert flight_request.max_results == 3


def test_preparation_input_normalizes_codes_but_preserves_city_names() -> None:
    """Only direct three-letter codes should be uppercased locally."""

    prepared = request(origin=" lhe ", destination=" London ")

    assert prepared.origin == "LHE"
    assert prepared.destination == "London"


def test_preparation_returns_choices_without_flight_provider_cost() -> None:
    """Ambiguous endpoints should stop before a priced flight search."""

    choices = AirportResolution(
        status="selection_required",
        query="London",
        options=[
            option("LHR", "Heathrow Airport"),
            option("LGW", "Gatwick Airport"),
        ],
    )
    service, airport_service, flight_service = create_service(
        origin=resolved("LHE", "LHE"),
        destination=choices,
    )

    result = asyncio.run(
        service.prepare_and_search(request=request(origin="LHE", destination="London"))
    )

    assert isinstance(result, FlightSearchPreparationGuidance)
    assert result.status == "airport_resolution_required"
    assert result.origin.iata_code == "LHE"
    assert [item.iata_code for item in result.destination.options] == [
        "LHR",
        "LGW",
    ]
    assert "destination" in result.message
    assert airport_service.resolve_airport.await_count == 2
    flight_service.search_flights.assert_not_awaited()


def test_preparation_reports_both_missing_route_endpoints() -> None:
    """Both unresolved locations should be represented in one response."""

    service, _, flight_service = create_service(
        origin=AirportResolution(status="not_found", query="Unknown origin"),
        destination=AirportResolution(
            status="not_found",
            query="Unknown destination",
        ),
    )

    result = asyncio.run(
        service.prepare_and_search(
            request=request(
                origin="Unknown origin",
                destination="Unknown destination",
            )
        )
    )

    assert isinstance(result, FlightSearchPreparationGuidance)
    assert "origin and destination" in result.message
    flight_service.search_flights.assert_not_awaited()


def test_preparation_stops_group_booking_before_airport_lookup() -> None:
    """Large parties should consume neither airport nor flight-provider quota."""

    group_result = FlightSearchResult(
        status=FlightSearchStatus.GROUP_BOOKING_REQUIRED,
        searched_at=NOW,
        message="Request group-booking assistance.",
    )
    service, airport_service, flight_service = create_service(
        origin=resolved("Lahore", "LHE"),
        destination=resolved("London", "LHR"),
    )
    flight_service.evaluate_request_policy.return_value = group_result

    result = asyncio.run(service.prepare_and_search(request=request(adults=10)))

    assert result is group_result
    airport_service.resolve_airport.assert_not_awaited()
    flight_service.search_flights.assert_not_awaited()


@pytest.mark.parametrize(
    "overrides",
    [
        {"origin": "London", "destination": " london "},
        {"return_date": date(2026, 9, 9)},
        {"adults": 1, "infants_on_lap_ages": [0, 1]},
        {"origin": "x"},
        {"destination": "x" * 121},
    ],
)
def test_preparation_input_rejects_invalid_request(
    overrides: dict[str, object],
) -> None:
    """Invalid facts should fail before any airport/provider request."""

    with pytest.raises(ValidationError):
        request(**overrides)


def test_guidance_rejects_two_resolved_airports() -> None:
    """Resolved routes belong to flight search, not clarification guidance."""

    with pytest.raises(ValidationError, match="unresolved location"):
        FlightSearchPreparationGuidance(
            origin=resolved("LHE", "LHE"),
            destination=resolved("LHR", "LHR"),
            message="Choose an airport.",
        )
