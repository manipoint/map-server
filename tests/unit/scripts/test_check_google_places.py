"""Tests for the live Google Places diagnostic script."""

import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

import scripts.check_google_places as script
from app.domain.places import PlaceSearchStatus
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.schemas import PlaceOption, PlaceSearchResult


class FakeAsyncClientContext:
    """Return one fake HTTP client from an async context manager."""

    def __init__(self, http_client: object) -> None:
        self.http_client = http_client
        self.exited = False

    async def __aenter__(self) -> object:
        return self.http_client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


def create_result() -> PlaceSearchResult:
    """Create one deterministic normalized Google Places result."""

    return PlaceSearchResult(
        status=PlaceSearchStatus.PLACES_AVAILABLE,
        searched_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
        location=ResolvedLocation(
            query="London, United Kingdom",
            display_name="London, United Kingdom",
            latitude=51.5071,
            longitude=-0.1276,
        ),
        places=[
            PlaceOption(
                provider_place_id="google-place-123",
                name="British Museum",
                summary="British Museum is a museum in London.",
                categories=["museum"],
                address="Great Russell Street, London",
                latitude=51.5194,
                longitude=-0.1270,
                source_urls=["https://maps.google.com/?cid=123"],
            )
        ],
    )


def test_check_google_places_assembles_searches_once_and_logs_result(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The script should make one service call and log non-reserved fields."""

    settings = MagicMock(log_level="INFO")
    fake_http_client = object()
    http_context = FakeAsyncClientContext(fake_http_client)
    location_provider = object()
    place_provider = object()
    service = MagicMock()
    service.search_places = AsyncMock(return_value=create_result())
    create_location_provider = MagicMock(return_value=location_provider)
    create_place_provider = MagicMock(return_value=place_provider)
    create_service = MagicMock(return_value=service)
    configure_logging = MagicMock()
    monkeypatch.setattr(script, "get_settings", MagicMock(return_value=settings))
    monkeypatch.setattr(script, "configure_logging", configure_logging)
    monkeypatch.setattr(
        script.httpx,
        "AsyncClient",
        MagicMock(return_value=http_context),
    )
    monkeypatch.setattr(
        script,
        "WeatherApiLocationClient",
        create_location_provider,
    )
    monkeypatch.setattr(script, "GooglePlacesClient", create_place_provider)
    monkeypatch.setattr(script, "PlaceSearchService", create_service)

    with caplog.at_level(logging.INFO, logger="scripts.check_google_places"):
        asyncio.run(
            script.check_google_places(
                destination=" London, United Kingdom ",
                interests=["Museums", "parks"],
                family_friendly=True,
            )
        )

    configure_logging.assert_called_once_with("INFO")
    create_location_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_place_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_service.assert_called_once_with(
        location_provider=location_provider,
        place_provider=place_provider,
    )
    request = service.search_places.await_args.kwargs["request"]
    assert request.destination == "London, United Kingdom"
    assert request.interests == ["Museums", "parks"]
    assert request.family_friendly is True
    assert request.max_results == 3
    assert http_context.exited is True
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Google Places check completed"
    )
    assert record.destination == "London, United Kingdom"
    assert record.status is PlaceSearchStatus.PLACES_AVAILABLE
    assert record.places[0]["name"] == "British Museum"
    assert record.result_message is None


def test_check_google_places_rejects_invalid_input_before_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid input should not load settings or consume provider quota."""

    get_settings = MagicMock()
    monkeypatch.setattr(script, "get_settings", get_settings)

    with pytest.raises(ValidationError):
        asyncio.run(
            script.check_google_places(
                destination=" ",
                interests=[],
                family_friendly=False,
            )
        )

    get_settings.assert_not_called()


def test_parse_arguments_returns_destination_interests_and_family_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should preserve destination preferences and boolean flag."""

    monkeypatch.setattr(
        "sys.argv",
        [
            "check_google_places",
            "London, United Kingdom",
            "museums",
            "parks",
            "--family-friendly",
        ],
    )

    arguments = script.parse_arguments()

    assert arguments.destination == "London, United Kingdom"
    assert arguments.interests == ["museums", "parks"]
    assert arguments.family_friendly is True
