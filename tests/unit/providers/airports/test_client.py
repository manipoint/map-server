"""Tests for the airport-provider contract."""

import inspect

from app.providers.airports.client import AirportProvider


def test_airport_provider_contract_exposes_async_search() -> None:
    """Provider implementations should expose one async lookup boundary."""

    assert inspect.iscoroutinefunction(AirportProvider.search_airports)


def test_airport_provider_contract_uses_normalized_schemas() -> None:
    """The provider boundary should accept and return bounded schema models."""

    annotations = inspect.get_annotations(AirportProvider.search_airports)

    assert annotations["request"].__module__ == "app.providers.airports.schemas"
    assert annotations["return"].__module__ == "app.providers.airports.schemas"
