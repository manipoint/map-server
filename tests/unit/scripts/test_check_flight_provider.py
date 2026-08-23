"""Tests for the live Duffel-provider diagnostic script."""

import asyncio
import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

import scripts.check_flight_provider as script
from app.domain.flights import FlightSearchStatus
from app.providers.flights.schemas import FlightSearchResult


class FakeAsyncClientContext:
    """Return one fake HTTP client from an async context manager."""

    def __init__(self, http_client: object) -> None:
        self.http_client = http_client
        self.exited = False

    async def __aenter__(self) -> object:
        return self.http_client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


def test_check_flight_provider_builds_request_and_prints_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The script should normalize input, search once, and print JSON output."""

    settings = object()
    fake_http_client = object()
    http_context = FakeAsyncClientContext(fake_http_client)
    result = FlightSearchResult(
        status=FlightSearchStatus.NO_OFFERS,
        searched_at=datetime(2026, 8, 23, 12, tzinfo=UTC),
        message="No current flight offers were found.",
    )
    provider = MagicMock()
    provider.search_flights = AsyncMock(return_value=result)
    create_provider = MagicMock(return_value=provider)
    monkeypatch.setattr(script, "get_settings", MagicMock(return_value=settings))
    monkeypatch.setattr(
        script.httpx,
        "AsyncClient",
        MagicMock(return_value=http_context),
    )
    monkeypatch.setattr(script, "DuffelFlightClient", create_provider)

    asyncio.run(
        script.check_flight_provider(
            origin=" lhe ",
            destination="jfk",
            departure_date=date(2026, 9, 10),
        )
    )

    create_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    request = provider.search_flights.await_args.kwargs["request"]
    assert request.origin == "LHE"
    assert request.destination == "JFK"
    assert request.departure_date == date(2026, 9, 10)
    assert request.max_results == 3
    assert http_context.exited is True
    assert json.loads(capsys.readouterr().out) == {
        "status": "no_offers",
        "searched_at": "2026-08-23T12:00:00Z",
        "offers": [],
        "message": "No current flight offers were found.",
    }


def test_check_flight_provider_rejects_invalid_route_before_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid input should not load configuration or create HTTP resources."""

    get_settings = MagicMock()
    monkeypatch.setattr(script, "get_settings", get_settings)

    with pytest.raises(ValidationError, match="must be different"):
        asyncio.run(
            script.check_flight_provider(
                origin="LHR",
                destination="LHR",
                departure_date=date(2026, 9, 10),
            )
        )

    get_settings.assert_not_called()


def test_parse_arguments_returns_typed_flight_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should parse IATA codes and one ISO departure date."""

    monkeypatch.setattr(
        "sys.argv",
        ["check_flight_provider", "LHR", "JFK", "2026-09-10"],
    )

    arguments = script.parse_arguments()

    assert arguments.origin == "LHR"
    assert arguments.destination == "JFK"
    assert arguments.departure_date == date(2026, 9, 10)
