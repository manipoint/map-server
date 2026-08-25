"""Tests for place-search orchestration."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import (
    AmbiguousLocationError,
    LocationNotFoundError,
    ProviderUnavailableError,
)
from app.domain.places import PlaceSearchStatus
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.schemas import (
    PlaceSearchInput,
    PlaceSearchResult,
)
from app.services.place_search_service import (
    LOCATION_CANDIDATE_LIMIT,
    PlaceSearchService,
)


def create_location(
    display_name: str = "London, United Kingdom",
) -> ResolvedLocation:
    """Create one deterministic location candidate."""

    return ResolvedLocation(
        query="London",
        display_name=display_name,
        latitude=51.5071,
        longitude=-0.1276,
    )


def create_result(location: ResolvedLocation) -> PlaceSearchResult:
    """Create one normalized no-places result for delegation tests."""

    return PlaceSearchResult(
        status=PlaceSearchStatus.NO_PLACES,
        searched_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
        location=location,
        message="No relevant places were found for this search.",
    )


def create_service(
    *,
    candidates: list[ResolvedLocation],
) -> tuple[PlaceSearchService, AsyncMock, AsyncMock]:
    """Create a service with deterministic async provider doubles."""

    location_provider = AsyncMock()
    location_provider.search_locations.return_value = candidates

    place_provider = AsyncMock()
    if candidates:
        place_provider.search_places.return_value = create_result(candidates[0])

    service = PlaceSearchService(
        location_provider=location_provider,
        place_provider=place_provider,
    )
    return service, location_provider, place_provider


def test_service_resolves_location_and_delegates_one_places_search() -> None:
    """One selected location should produce exactly one provider search."""

    location = create_location()
    service, location_provider, place_provider = create_service(candidates=[location])
    request = PlaceSearchInput(
        destination="London",
        interests=["museums", "parks"],
        max_results=3,
    )

    result = asyncio.run(service.search_places(request=request))

    assert result.status is PlaceSearchStatus.NO_PLACES
    location_provider.search_locations.assert_awaited_once_with(
        query="London",
        max_results=LOCATION_CANDIDATE_LIMIT,
    )
    place_provider.search_places.assert_awaited_once()
    resolved_search = place_provider.search_places.await_args.kwargs["search"]
    assert resolved_search.request is request
    assert resolved_search.location is location


def test_service_stops_before_places_call_when_location_is_missing() -> None:
    """An unresolved destination should consume no Google Places quota."""

    service, location_provider, place_provider = create_service(candidates=[])

    with pytest.raises(LocationNotFoundError):
        asyncio.run(
            service.search_places(
                request=PlaceSearchInput(destination="Unknown destination")
            )
        )

    location_provider.search_locations.assert_awaited_once()
    place_provider.search_places.assert_not_awaited()


def test_service_stops_before_places_call_when_location_is_ambiguous() -> None:
    """Ambiguous destinations should require selection before paid discovery."""

    service, _, place_provider = create_service(
        candidates=[
            create_location("London, United Kingdom"),
            create_location("London, Ontario, Canada"),
        ]
    )

    with pytest.raises(AmbiguousLocationError):
        asyncio.run(
            service.search_places(request=PlaceSearchInput(destination="London"))
        )

    place_provider.search_places.assert_not_awaited()


def test_service_propagates_safe_provider_failure() -> None:
    """A safe provider outage should pass through without another attempt."""

    service, _, place_provider = create_service(candidates=[create_location()])
    place_provider.search_places.side_effect = ProviderUnavailableError(
        "Places provider is unavailable"
    )

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        asyncio.run(
            service.search_places(request=PlaceSearchInput(destination="London"))
        )

    place_provider.search_places.assert_awaited_once()
