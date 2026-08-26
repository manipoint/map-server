"""Tests for flight-search orchestration and group-booking policy."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import InvalidTravelDateError, ProviderUnavailableError
from app.domain.flights import FlightSearchStatus
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult
from app.services.flight_search_service import FlightSearchService

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def create_request(**overrides: object) -> FlightSearchInput:
    """Create one valid flight request relative to the fixed test clock."""

    values: dict[str, object] = {
        "origin": "LHR",
        "destination": "JFK",
        "departure_date": NOW.date() + timedelta(days=1),
        "return_date": NOW.date() + timedelta(days=5),
        "adults": 1,
    }
    values.update(overrides)
    return FlightSearchInput.model_validate(values)


def create_provider_result() -> FlightSearchResult:
    """Create one normalized provider response for delegation tests."""

    return FlightSearchResult(
        status=FlightSearchStatus.NO_OFFERS,
        searched_at=NOW,
        message="No current offers were found.",
    )


def create_service(
    *,
    traveler_limit: int = 9,
) -> tuple[FlightSearchService, AsyncMock]:
    """Create a service backed by one deterministic provider double."""

    provider = AsyncMock()
    provider.search_flights.return_value = create_provider_result()
    service = FlightSearchService(
        flight_provider=provider,
        self_service_traveler_limit=traveler_limit,
        clock=lambda: NOW,
    )
    return service, provider


@pytest.mark.parametrize("traveler_limit", [0, -1])
def test_service_rejects_invalid_traveler_limit(traveler_limit: int) -> None:
    """Invalid policy configuration should fail during construction."""

    with pytest.raises(ValueError, match="must be at least 1"):
        create_service(traveler_limit=traveler_limit)


def test_service_rejects_past_departure_without_provider_call() -> None:
    """Past travel should consume no provider quota."""

    service, provider = create_service()
    request = create_request(departure_date=NOW.date() - timedelta(days=1))

    with pytest.raises(
        InvalidTravelDateError,
        match="Flight departure date cannot be in the past",
    ):
        asyncio.run(service.search_flights(request=request))

    provider.search_flights.assert_not_awaited()


def test_service_accepts_departure_today() -> None:
    """A same-day search should still reach the configured provider."""

    service, provider = create_service()
    request = create_request(departure_date=NOW.date())

    result = asyncio.run(service.search_flights(request=request))

    assert result is provider.search_flights.return_value
    provider.search_flights.assert_awaited_once_with(request=request)


def test_service_delegates_exactly_nine_travelers() -> None:
    """The self-service boundary itself should remain searchable."""

    service, provider = create_service()
    request = create_request(adults=9)

    result = asyncio.run(service.search_flights(request=request))

    assert result.status is FlightSearchStatus.NO_OFFERS
    provider.search_flights.assert_awaited_once_with(request=request)


def test_service_returns_group_guidance_without_provider_call() -> None:
    """A party above the boundary should avoid an external flight request."""

    service, provider = create_service()
    request = create_request(adults=10)

    result = asyncio.run(service.search_flights(request=request))

    assert result.status is FlightSearchStatus.GROUP_BOOKING_REQUIRED
    assert result.searched_at == NOW
    assert result.offers == []
    assert result.message is not None
    assert "up to 9 travelers" in result.message
    assert "Hotel, places, and weather planning can continue" in result.message
    provider.search_flights.assert_not_awaited()


def test_service_counts_all_traveler_categories_for_group_policy() -> None:
    """Children and infants should count toward the provider party boundary."""

    service, provider = create_service()
    request = create_request(
        adults=2,
        children_ages=[4, 6, 8, 10, 12, 14],
        infants_with_seat_ages=[1],
        infants_on_lap_ages=[0],
    )

    assert request.total_travelers == 10

    result = asyncio.run(service.search_flights(request=request))

    assert result.status is FlightSearchStatus.GROUP_BOOKING_REQUIRED
    provider.search_flights.assert_not_awaited()


def test_service_honors_custom_provider_limit() -> None:
    """A future provider should be able to supply its own group boundary."""

    service, provider = create_service(traveler_limit=3)

    result = asyncio.run(service.search_flights(request=create_request(adults=4)))

    assert result.status is FlightSearchStatus.GROUP_BOOKING_REQUIRED
    assert result.message is not None
    assert "up to 3 travelers" in result.message
    provider.search_flights.assert_not_awaited()


def test_service_propagates_provider_failure() -> None:
    """Central exception handling should receive normalized provider failures."""

    service, provider = create_service()
    provider.search_flights.side_effect = ProviderUnavailableError(
        "Flight provider is unavailable"
    )

    with pytest.raises(
        ProviderUnavailableError,
        match="Flight provider is unavailable",
    ):
        asyncio.run(service.search_flights(request=create_request()))
