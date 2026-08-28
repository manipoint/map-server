"""Tests for canonical location resolution orchestration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.trips import CanonicalLocation
from app.providers.locations.canonical_client import CanonicalLocationProvider
from app.services.location_resolution_service import LocationResolutionService


def test_resolve_normalizes_query_and_calls_provider_once() -> None:
    """One explicit resolution should produce exactly one provider operation."""

    expected = CanonicalLocation(
        provider="google",
        provider_location_id="london-id",
        canonical_name="London, United Kingdom",
        country_code="GB",
        latitude=51.5074,
        longitude=-0.1278,
    )
    provider = MagicMock(spec=CanonicalLocationProvider)
    provider.search_canonical_locations = AsyncMock(return_value=[expected])
    service = LocationResolutionService(provider=provider)

    result = asyncio.run(service.resolve(query="  London  ", max_results=3))

    assert result == [expected]
    provider.search_canonical_locations.assert_awaited_once_with(
        query="London",
        max_results=3,
    )


@pytest.mark.parametrize(
    ("query", "max_results", "message"),
    [
        (" ", 5, "query"),
        ("x" * 121, 5, "query"),
        ("London", 0, "max_results"),
        ("London", 6, "max_results"),
    ],
)
def test_resolve_rejects_invalid_input_before_provider_call(
    query: str,
    max_results: int,
    message: str,
) -> None:
    """Invalid or cost-unbounded input should not consume provider quota."""

    provider = MagicMock(spec=CanonicalLocationProvider)
    provider.search_canonical_locations = AsyncMock()
    service = LocationResolutionService(provider=provider)

    with pytest.raises(ValueError, match=message):
        asyncio.run(service.resolve(query=query, max_results=max_results))

    provider.search_canonical_locations.assert_not_awaited()
