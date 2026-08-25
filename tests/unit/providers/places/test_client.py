"""Tests for the place-discovery provider contract."""

import inspect

from app.providers.places.client import PlaceProvider
from app.providers.places.schemas import PlaceSearchResult, ResolvedPlaceSearch


def test_place_provider_contract_exposes_async_search() -> None:
    """Provider implementations should expose one asynchronous search boundary."""

    assert inspect.iscoroutinefunction(PlaceProvider.search_places)


def test_place_provider_uses_only_provider_independent_schemas() -> None:
    """The provider contract should not expose Tavily or MCP transport models."""

    annotations = inspect.get_annotations(PlaceProvider.search_places)

    assert annotations["search"] is ResolvedPlaceSearch
    assert annotations["return"] is PlaceSearchResult
