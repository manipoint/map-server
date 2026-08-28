"""Tests for the Google Places Text Search transport adapter."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.domain.places import PlaceSearchStatus
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.google_client import (
    GOOGLE_LOCATION_FIELD_MASK,
    GOOGLE_PLACES_FIELD_MASK,
    GooglePlacesClient,
)
from app.providers.places.google_schemas import GooglePlaceTextSearchRequest
from app.providers.places.schemas import PlaceSearchInput, ResolvedPlaceSearch


def create_settings(**overrides: object) -> Settings:
    """Create isolated settings with Google place discovery enabled."""

    values: dict[str, object] = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
        "places_provider": "google",
        "google_places_api_key": SecretStr("test-google-places-key"),
        "google_places_text_search_url": (
            "https://places.googleapis.test/v1/places:searchText"
        ),
        "provider_timeout_seconds": 7.0,
    }
    values.update(overrides)
    return Settings(**values)


def create_response_payload(**overrides: object) -> dict[str, object]:
    """Create one minimal valid Google Places response."""

    values: dict[str, object] = {
        "places": [
            {
                "id": "google-place-123",
                "displayName": {"text": "British Museum", "languageCode": "en"},
                "formattedAddress": "Great Russell Street, London",
                "location": {"latitude": 51.5194, "longitude": -0.1270},
                "types": ["museum", "tourist_attraction"],
                "primaryType": "museum",
                "googleMapsUri": "https://maps.google.com/?cid=123",
            }
        ]
    }
    values.update(overrides)
    return values


def run_search(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
) -> object:
    """Run one deterministic Google request through an in-memory transport."""

    async def exercise() -> object:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = GooglePlacesClient(
                http_client=http_client,
                settings=create_settings(),
            )
            return await client.search(
                request=GooglePlaceTextSearchRequest(
                    text_query="Museums in London, United Kingdom",
                    page_size=5,
                    rank_preference="RELEVANCE",
                )
            )

    return asyncio.run(exercise())


def test_google_client_rejects_disabled_provider() -> None:
    """Construction should fail when Google place discovery is disabled."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="not configured"):
                GooglePlacesClient(
                    http_client=http_client,
                    settings=create_settings(
                        places_provider=None,
                        google_places_api_key=None,
                    ),
                )

    asyncio.run(exercise())


def test_google_client_rejects_credentials_removed_after_validation() -> None:
    """Construction should defend against credentials removed at runtime."""

    async def exercise() -> None:
        settings = create_settings().model_copy(update={"google_places_api_key": None})
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="credentials"):
                GooglePlacesClient(http_client=http_client, settings=settings)

    asyncio.run(exercise())


def test_google_search_sends_exact_headers_and_bounded_json() -> None:
    """One request should use the fixed field mask and bounded camel-case body."""

    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json=create_response_payload(), request=request)

    result = run_search(handler=handler)

    assert captured_request is not None
    assert str(captured_request.url) == (
        "https://places.googleapis.test/v1/places:searchText"
    )
    assert captured_request.headers["X-Goog-Api-Key"] == ("test-google-places-key")
    assert captured_request.headers["X-Goog-FieldMask"] == (GOOGLE_PLACES_FIELD_MASK)
    assert "places.photos" not in GOOGLE_PLACES_FIELD_MASK
    assert "places.reviews" not in GOOGLE_PLACES_FIELD_MASK
    assert "places.websiteUri" not in GOOGLE_PLACES_FIELD_MASK
    assert json.loads(captured_request.content) == {
        "textQuery": "Museums in London, United Kingdom",
        "pageSize": 5,
        "languageCode": "en",
        "strictTypeFiltering": False,
        "rankPreference": "RELEVANCE",
    }
    assert "test-google-places-key" not in captured_request.content.decode()
    assert result.places[0].display_name.text == "British Museum"


def test_google_search_places_makes_one_call_and_normalizes_result() -> None:
    """The provider contract should perform one request and map its response."""

    request_count = 0
    searched_at = datetime(2026, 8, 25, 12, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=create_response_payload(), request=request)

    async def exercise() -> object:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = GooglePlacesClient(
                http_client=http_client,
                settings=create_settings(),
                clock=lambda: searched_at,
            )
            return await client.search_places(
                search=ResolvedPlaceSearch(
                    request=PlaceSearchInput(
                        destination="London",
                        interests=["museums"],
                        max_results=3,
                    ),
                    location=ResolvedLocation(
                        query="London",
                        display_name="London, United Kingdom",
                        latitude=51.5071,
                        longitude=-0.1276,
                    ),
                )
            )

    result = asyncio.run(exercise())

    assert request_count == 1
    assert result.status is PlaceSearchStatus.PLACES_AVAILABLE
    assert result.searched_at == searched_at
    assert result.places[0].name == "British Museum"


def test_google_location_search_makes_one_cost_bounded_call() -> None:
    """Canonical resolution should use one call and only required fields."""

    captured_request: httpx.Request | None = None
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request, request_count
        captured_request = request
        request_count += 1
        return httpx.Response(
            200,
            json=create_response_payload(
                places=[
                    {
                        "id": "london-id",
                        "displayName": {"text": "London", "languageCode": "en"},
                        "formattedAddress": "London, United Kingdom",
                        "location": {"latitude": 51.5074, "longitude": -0.1278},
                        "types": ["locality", "political"],
                        "addressComponents": [
                            {
                                "longText": "United Kingdom",
                                "shortText": "GB",
                                "types": ["country", "political"],
                            }
                        ],
                    }
                ]
            ),
            request=request,
        )

    async def exercise() -> object:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = GooglePlacesClient(
                http_client=http_client,
                settings=create_settings(),
            )
            return await client.search_canonical_locations(
                query="  London  ",
                max_results=3,
            )

    result = asyncio.run(exercise())

    assert request_count == 1
    assert captured_request is not None
    assert captured_request.headers["X-Goog-FieldMask"] == (GOOGLE_LOCATION_FIELD_MASK)
    assert "places.photos" not in GOOGLE_LOCATION_FIELD_MASK
    assert "places.reviews" not in GOOGLE_LOCATION_FIELD_MASK
    assert json.loads(captured_request.content) == {
        "textQuery": "London",
        "pageSize": 3,
        "languageCode": "en",
        "strictTypeFiltering": False,
    }
    assert len(result) == 1
    assert result[0].provider_location_id == "london-id"
    assert result[0].country_code == "GB"


@pytest.mark.parametrize("status_code", [401, 403])
def test_google_search_reports_rejected_credentials(status_code: int) -> None:
    """Authentication failures should be distinct from provider outages."""

    with pytest.raises(
        ProviderConfigurationError,
        match="credentials were rejected",
    ):
        run_search(handler=lambda request: httpx.Response(status_code, request=request))


@pytest.mark.parametrize("status_code", [400, 422, 429, 500, 503])
def test_google_search_reports_provider_http_failure(status_code: int) -> None:
    """Non-authentication HTTP failures should become safe outages."""

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=lambda request: httpx.Response(status_code, request=request))


def test_google_search_reports_network_failure() -> None:
    """Transport errors should not leak private connection details."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private connection detail", request=request)

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=handler)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(
            200,
            json={
                "places": [
                    {
                        "id": "google-place-123",
                        "displayName": {"text": "British Museum"},
                        "location": {"latitude": 200, "longitude": 0},
                    }
                ]
            },
        ),
    ],
)
def test_google_search_reports_invalid_provider_response(
    response: httpx.Response,
) -> None:
    """Malformed JSON and schemas should become safe provider failures."""

    with pytest.raises(
        ProviderUnavailableError,
        match="returned an invalid response",
    ):
        run_search(handler=lambda request: response)
