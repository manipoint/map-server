"""Tests for the live airport-resolution diagnostic script."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

import scripts.check_airport_provider as script
from app.providers.airports.schemas import AirportOption, AirportResolution


class FakeAsyncClientContext:
    """Return one fake HTTP client from an async context manager."""

    def __init__(self, http_client: object) -> None:
        self.http_client = http_client
        self.exited = False

    async def __aenter__(self) -> object:
        return self.http_client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


def test_check_airport_provider_assembles_resolves_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The diagnostic should perform one bounded lookup and log safe fields."""

    settings = MagicMock(log_level="INFO")
    http_client = object()
    http_context = FakeAsyncClientContext(http_client)
    provider = object()
    service = MagicMock()
    service.resolve_airport = AsyncMock(
        return_value=AirportResolution(
            status="selection_required",
            query="London",
            options=[
                AirportOption(
                    provider_location_id="apt_lhr",
                    iata_code="LHR",
                    location_type="airport",
                    name="Heathrow Airport",
                    city_name="London",
                    country_name="United Kingdom",
                    country_code="GB",
                ),
                AirportOption(
                    provider_location_id="apt_lgw",
                    iata_code="LGW",
                    location_type="airport",
                    name="Gatwick Airport",
                    city_name="London",
                    country_name="United Kingdom",
                    country_code="GB",
                ),
            ],
        )
    )
    create_provider = MagicMock(return_value=provider)
    create_service = MagicMock(return_value=service)
    configure_logging = MagicMock()
    monkeypatch.setattr(script, "get_settings", MagicMock(return_value=settings))
    monkeypatch.setattr(script, "configure_logging", configure_logging)
    monkeypatch.setattr(
        script.httpx,
        "AsyncClient",
        MagicMock(return_value=http_context),
    )
    monkeypatch.setattr(script, "DuffelAirportClient", create_provider)
    monkeypatch.setattr(script, "AirportResolutionService", create_service)

    with caplog.at_level(logging.INFO, logger="scripts.check_airport_provider"):
        asyncio.run(script.check_airport_provider(query=" London "))

    configure_logging.assert_called_once_with("INFO")
    create_provider.assert_called_once_with(
        http_client=http_client,
        settings=settings,
    )
    create_service.assert_called_once_with(airport_provider=provider)
    request = service.resolve_airport.await_args.kwargs["request"]
    assert request.query == "London"
    assert request.max_results == 5
    assert http_context.exited is True
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Airport resolution check completed"
    )
    assert record.airport_query == "London"
    assert record.resolution_status == "selection_required"
    assert [option["iata_code"] for option in record.options] == ["LHR", "LGW"]


def test_check_airport_provider_rejects_invalid_input_before_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid input should fail locally without loading settings or quota use."""

    get_settings = MagicMock()
    monkeypatch.setattr(script, "get_settings", get_settings)

    with pytest.raises(ValidationError):
        asyncio.run(script.check_airport_provider(query=" "))

    get_settings.assert_not_called()


def test_parse_arguments_returns_airport_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should preserve a city or airport name as one argument."""

    monkeypatch.setattr("sys.argv", ["check_airport_provider", "London"])

    arguments = script.parse_arguments()

    assert arguments.query == "London"
