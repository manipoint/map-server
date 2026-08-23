"""Tests for the flight-provider contract."""

import inspect

from app.providers.flights.client import FlightProvider


def test_flight_provider_contract_exposes_async_search() -> None:
    """Provider implementations must expose one asynchronous search boundary."""

    assert inspect.iscoroutinefunction(FlightProvider.search_flights)


def test_flight_provider_does_not_depend_on_mcp_transport() -> None:
    """The provider contract should remain reusable outside the MCP layer."""

    annotations = inspect.get_annotations(FlightProvider.search_flights)

    assert annotations["request"].__module__ == "app.providers.flights.schemas"
