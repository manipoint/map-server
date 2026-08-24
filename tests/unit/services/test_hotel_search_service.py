"""Tests for hotel-search orchestration."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from app.common.exceptions import (
    AmbiguousLocationError,
    InvalidTravelDateError,
    LocationNotFoundError,
    ProviderUnavailableError,
)
from app.domain.hotels import HotelSearchStatus
from app.providers.hotels.schemas import (
    HotelSearchInput,
    HotelSearchResult,
    ResolvedHotelSearch,
)
from app.providers.locations.schemas import ResolvedLocation
from app.services.hotel_search_service import (
    LOCATION_CANDIDATE_LIMIT,
    HotelSearchService,
)

TODAY = date(2026, 8, 24)


def create_request(
    *,
    destination: str = "London",
    check_in_date: date = date(2026, 9, 10),
    **overrides: object,
) -> HotelSearchInput:
    """Create one valid hotel request relative to the fixed test clock."""

    values: dict[str, object] = {
        "destination": destination,
        "check_in_date": check_in_date,
        "check_out_date": check_in_date + timedelta(days=2),
    }
    values.update(overrides)
    return HotelSearchInput(**values)


def create_location(
    display_name: str = "London, United Kingdom",
    *,
    latitude: float = 51.5071,
    longitude: float = -0.1276,
) -> ResolvedLocation:
    """Create one deterministic location candidate."""

    return ResolvedLocation(
        query="London",
        display_name=display_name,
        latitude=latitude,
        longitude=longitude,
    )


def create_result(location: ResolvedLocation) -> HotelSearchResult:
    """Create one normalized no-availability result for delegation tests."""

    return HotelSearchResult(
        status=HotelSearchStatus.NO_HOTELS,
        searched_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        location=location,
        message="No current hotels were found for this search.",
    )


def create_service(
    *,
    candidates: list[ResolvedLocation] | None = None,
    radius_km: int = 5,
) -> tuple[HotelSearchService, AsyncMock, AsyncMock]:
    """Create a service with deterministic async provider doubles."""

    selected_candidates = [create_location()] if candidates is None else candidates
    location_provider = AsyncMock()
    location_provider.search_locations.return_value = selected_candidates

    hotel_provider = AsyncMock()
    if selected_candidates:
        hotel_provider.search_hotels.return_value = create_result(
            selected_candidates[0]
        )

    service = HotelSearchService(
        location_provider=location_provider,
        hotel_provider=hotel_provider,
        radius_km=radius_km,
        clock=lambda: TODAY,
    )
    return service, location_provider, hotel_provider


@pytest.mark.parametrize("radius_km", [0, 101])
def test_service_rejects_invalid_search_radius(radius_km: int) -> None:
    """Invalid configuration should fail when the service is constructed."""

    with pytest.raises(ValueError, match="between 1 and 100"):
        create_service(radius_km=radius_km)


@pytest.mark.parametrize("radius_km", [1, 100])
def test_service_accepts_radius_boundaries(radius_km: int) -> None:
    """Documented Duffel radius boundaries should remain usable."""

    service, _, _ = create_service(radius_km=radius_km)

    assert service.radius_km == radius_km


@pytest.mark.parametrize(
    ("check_in_date", "message"),
    [
        (TODAY - timedelta(days=1), "cannot be in the past"),
        (TODAY + timedelta(days=331), "more than 330 days ahead"),
    ],
)
def test_service_rejects_unsearchable_dates_before_provider_calls(
    check_in_date: date,
    message: str,
) -> None:
    """Invalid dates should consume neither geocoding nor hotel quota."""

    service, location_provider, hotel_provider = create_service()

    with pytest.raises(InvalidTravelDateError, match=message):
        asyncio.run(
            service.search_hotels(request=create_request(check_in_date=check_in_date))
        )

    location_provider.search_locations.assert_not_awaited()
    hotel_provider.search_hotels.assert_not_awaited()


@pytest.mark.parametrize("days_ahead", [0, 330])
def test_service_accepts_date_window_boundaries(days_ahead: int) -> None:
    """Today and exactly 330 days ahead should reach provider resolution."""

    service, location_provider, hotel_provider = create_service()

    result = asyncio.run(
        service.search_hotels(
            request=create_request(check_in_date=TODAY + timedelta(days=days_ahead))
        )
    )

    assert result.status is HotelSearchStatus.NO_HOTELS
    location_provider.search_locations.assert_awaited_once()
    hotel_provider.search_hotels.assert_awaited_once()


def test_service_reports_location_not_found_without_hotel_call() -> None:
    """An empty location search should stop before Duffel work."""

    service, location_provider, hotel_provider = create_service(candidates=[])

    with pytest.raises(LocationNotFoundError, match="No location matched"):
        asyncio.run(service.search_hotels(request=create_request()))

    location_provider.search_locations.assert_awaited_once_with(
        query="London",
        max_results=LOCATION_CANDIDATE_LIMIT,
    )
    hotel_provider.search_hotels.assert_not_awaited()


def test_service_reports_ranked_ambiguous_candidates() -> None:
    """A broad destination should return choices instead of guessing."""

    candidates = [
        create_location("London, United Kingdom"),
        create_location(
            "London, Ontario, Canada",
            latitude=42.9834,
            longitude=-81.233,
        ),
    ]
    service, _, hotel_provider = create_service(candidates=candidates)

    with pytest.raises(AmbiguousLocationError) as error_info:
        asyncio.run(service.search_hotels(request=create_request()))

    assert error_info.value.candidates == (
        "London, United Kingdom",
        "London, Ontario, Canada",
    )
    hotel_provider.search_hotels.assert_not_awaited()


def test_service_accepts_case_insensitive_exact_candidate_retry() -> None:
    """A user-selected full display name should resolve without another prompt."""

    candidates = [
        create_location("London, United Kingdom"),
        create_location(
            "London, Ontario, Canada",
            latitude=42.9834,
            longitude=-81.233,
        ),
    ]
    service, _, hotel_provider = create_service(candidates=candidates)
    request = create_request(destination="london, united kingdom")

    result = asyncio.run(service.search_hotels(request=request))

    assert result.location == candidates[0]
    hotel_provider.search_hotels.assert_awaited_once()


def test_service_builds_resolved_search_and_preserves_request() -> None:
    """The selected coordinates and complete party should reach the hotel provider."""

    location = create_location()
    service, location_provider, hotel_provider = create_service(
        candidates=[location],
        radius_km=7,
    )
    request = create_request(
        adults=1,
        children_ages=[4, 8],
        free_cancellation_only=True,
        max_results=3,
    )

    result = asyncio.run(service.search_hotels(request=request))

    location_provider.search_locations.assert_awaited_once_with(
        query="London",
        max_results=LOCATION_CANDIDATE_LIMIT,
    )
    delegated_search = hotel_provider.search_hotels.await_args.kwargs["search"]
    assert isinstance(delegated_search, ResolvedHotelSearch)
    assert delegated_search.request is request
    assert delegated_search.location is location
    assert delegated_search.radius_km == 7
    assert result.location is location


@pytest.mark.parametrize("provider_name", ["location", "hotel"])
def test_service_preserves_provider_errors(provider_name: str) -> None:
    """Safe provider errors should remain available to graph error handling."""

    service, location_provider, hotel_provider = create_service()
    provider_error = ProviderUnavailableError("Provider is unavailable")

    if provider_name == "location":
        location_provider.search_locations.side_effect = provider_error
    else:
        hotel_provider.search_hotels.side_effect = provider_error

    with pytest.raises(ProviderUnavailableError) as error_info:
        asyncio.run(service.search_hotels(request=create_request()))

    assert error_info.value is provider_error


def test_service_reads_clock_once_per_search() -> None:
    """One operation should use a consistent date boundary."""

    location = create_location()
    location_provider = AsyncMock()
    location_provider.search_locations.return_value = [location]
    hotel_provider = AsyncMock()
    hotel_provider.search_hotels.return_value = create_result(location)
    clock = Mock(return_value=TODAY)
    service = HotelSearchService(
        location_provider=location_provider,
        hotel_provider=hotel_provider,
        radius_km=5,
        clock=clock,
    )

    asyncio.run(service.search_hotels(request=create_request()))

    clock.assert_called_once_with()
