"""Tests for the live Tavily place-discovery diagnostic script."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

import scripts.check_tavily_places as script
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.tavily_schemas import (
    TavilyPlaceSearchItem,
    TavilyPlaceSearchRequest,
    TavilyPlaceSearchResponse,
)


class FakeAsyncClientContext:
    """Return one fake HTTP client from an async context manager."""

    def __init__(self, http_client: object) -> None:
        self.http_client = http_client
        self.exited = False

    async def __aenter__(self) -> object:
        return self.http_client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


def create_location() -> ResolvedLocation:
    """Create one resolved London destination."""

    return ResolvedLocation(
        query="London, United Kingdom",
        display_name="London, United Kingdom",
        latitude=51.5071,
        longitude=-0.1276,
    )


def test_check_tavily_places_resolves_searches_and_logs_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The script should log ranked links without raw content or credentials."""

    settings = MagicMock(log_level="INFO")
    fake_http_client = object()
    http_context = FakeAsyncClientContext(fake_http_client)
    location = create_location()
    location_client = MagicMock()
    location_client.search_locations = AsyncMock(return_value=[location])
    tavily_client = MagicMock()
    response = TavilyPlaceSearchResponse(
        query="Best places to visit in London, United Kingdom",
        results=[
            TavilyPlaceSearchItem(
                title="British Museum",
                url="https://www.britishmuseum.org/visit",
                content="Untrusted provider content that must not be logged.",
                score=0.91,
            )
        ],
        request_id="request-123",
    )
    tavily_client.search = AsyncMock(return_value=response)
    provider_request = TavilyPlaceSearchRequest(
        query="Best places to visit in London, United Kingdom",
        max_results=5,
    )
    create_location_client = MagicMock(return_value=location_client)
    create_tavily_client = MagicMock(return_value=tavily_client)
    build_request = MagicMock(return_value=provider_request)
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
        create_location_client,
    )
    monkeypatch.setattr(script, "TavilySearchClient", create_tavily_client)
    monkeypatch.setattr(script, "build_tavily_place_search", build_request)

    with caplog.at_level(logging.INFO, logger="scripts.check_tavily_places"):
        asyncio.run(
            script.check_tavily_places(
                destination=" London, United Kingdom ",
                interests=[" Museums ", "parks"],
            )
        )

    configure_logging.assert_called_once_with("INFO")
    create_location_client.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_tavily_client.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    location_client.search_locations.assert_awaited_once_with(
        query="London, United Kingdom",
        max_results=5,
    )
    normalized_request = build_request.call_args.kwargs["request"]
    assert normalized_request.interests == ["Museums", "parks"]
    assert build_request.call_args.kwargs["location"] is location
    tavily_client.search.assert_awaited_once_with(request=provider_request)
    assert http_context.exited is True

    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Tavily places check completed"
    )
    assert record.destination == "London, United Kingdom"
    assert record.tavily_request_id == "request-123"
    assert record.results == [
        {
            "title": "British Museum",
            "url": "https://www.britishmuseum.org/visit",
            "score": 0.91,
        }
    ]
    assert "Untrusted provider content" not in caplog.text


def test_check_tavily_places_rejects_invalid_input_before_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid destinations should not spend location or search requests."""

    get_settings = MagicMock()
    monkeypatch.setattr(script, "get_settings", get_settings)

    with pytest.raises(ValidationError):
        asyncio.run(
            script.check_tavily_places(
                destination=" ",
                interests=[],
            )
        )

    get_settings.assert_not_called()


def test_parse_arguments_returns_destination_and_interests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should parse one destination and zero or more interests."""

    monkeypatch.setattr(
        "sys.argv",
        [
            "check_tavily_places",
            "London, United Kingdom",
            "museums",
            "parks",
        ],
    )

    arguments = script.parse_arguments()

    assert arguments.destination == "London, United Kingdom"
    assert arguments.interests == ["museums", "parks"]
