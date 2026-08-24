"""Tests for the WeatherAPI deterministic location-search adapter."""

import asyncio
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.providers.locations.schemas import ResolvedLocation
from app.providers.locations.weatherapi_client import (
    WeatherApiLocationClient,
    build_display_name,
)
from app.providers.locations.weatherapi_schemas import (
    WeatherApiLocationCandidate,
)


def create_settings(**overrides: object) -> Settings:
    """Create valid isolated settings for location-client tests."""

    values: dict[str, object] = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
        "weather_api_key": SecretStr("test-weather-key"),
    }
    values.update(overrides)
    return Settings(**values)


def create_candidate(**overrides: object) -> dict[str, object]:
    """Create one realistic WeatherAPI search candidate."""

    values: dict[str, object] = {
        "id": 2801268,
        "name": "London",
        "region": "City of London, Greater London",
        "country": "United Kingdom",
        "lat": 51.5171,
        "lon": -0.1062,
        "url": "london-city-of-london-greater-london-united-kingdom",
    }
    values.update(overrides)
    return values


def run_search(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    query: str = "London",
    max_results: int = 5,
) -> list[ResolvedLocation]:
    """Run one deterministic location search through in-memory HTTP."""

    async def exercise() -> list[ResolvedLocation]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = WeatherApiLocationClient(
                http_client=http_client,
                settings=create_settings(),
            )
            return await client.search_locations(
                query=query,
                max_results=max_results,
            )

    return asyncio.run(exercise())


def test_build_display_name_removes_blank_and_duplicate_parts() -> None:
    """Repeated city/country labels should not create noisy display names."""

    candidate = WeatherApiLocationCandidate(
        name="Singapore",
        region="   ",
        country="singapore",
        lat=1.2897,
        lon=103.8501,
    )

    assert build_display_name(candidate) == "Singapore"


def test_location_client_sends_key_query_and_returns_ranked_candidates() -> None:
    """Search should preserve provider ranking while removing duplicate places."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                create_candidate(),
                create_candidate(name="Duplicate London"),
                create_candidate(
                    name="London",
                    region="Ontario",
                    country="Canada",
                    lat=42.9834,
                    lon=-81.233,
                ),
                create_candidate(
                    name="London",
                    region="Ohio",
                    country="United States of America",
                    lat=39.8864,
                    lon=-83.4483,
                ),
            ],
        )

    locations = run_search(
        handler=handler,
        query="  London  ",
        max_results=2,
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/search.json"
    assert request.url.params["key"] == "test-weather-key"
    assert request.url.params["q"] == "London"
    assert [location.display_name for location in locations] == [
        "London, City of London, Greater London, United Kingdom",
        "London, Ontario, Canada",
    ]
    assert all(location.query == "London" for location in locations)


def test_location_client_returns_empty_candidates() -> None:
    """A valid empty provider response should represent no matching location."""

    locations = run_search(
        handler=lambda request: httpx.Response(200, json=[]),
    )

    assert locations == []


def test_location_client_bounds_long_display_name() -> None:
    """Verbose provider labels must fit the normalized WebSocket contract."""

    locations = run_search(
        handler=lambda request: httpx.Response(
            200,
            json=[
                create_candidate(
                    name="N" * 120,
                    region="R" * 120,
                    country="C" * 120,
                )
            ],
        )
    )

    assert len(locations[0].display_name) == 200


@pytest.mark.parametrize(
    ("query", "max_results", "message"),
    [
        (" ", 5, "between 2 and 120"),
        ("A", 5, "between 2 and 120"),
        ("x" * 121, 5, "between 2 and 120"),
        ("London", 0, "between 1 and 10"),
        ("London", 11, "between 1 and 10"),
    ],
)
def test_location_client_rejects_invalid_input_before_http(
    query: str,
    max_results: int,
    message: str,
) -> None:
    """Invalid application input should not consume provider quota."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=[])

    with pytest.raises(ValueError, match=message):
        run_search(
            handler=handler,
            query=query,
            max_results=max_results,
        )

    assert request_count == 0


def test_location_client_rejects_missing_configuration() -> None:
    """Missing credentials should fail before any provider request."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(
                ProviderConfigurationError,
                match="not configured",
            ):
                WeatherApiLocationClient(
                    http_client=http_client,
                    settings=create_settings(weather_api_key=None),
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("status_code", [401, 403])
def test_location_client_reports_rejected_credentials(status_code: int) -> None:
    """Provider authentication failures should remain distinguishable."""

    with pytest.raises(
        ProviderConfigurationError,
        match="credentials were rejected",
    ):
        run_search(
            handler=lambda request: httpx.Response(
                status_code,
                request=request,
            )
        )


@pytest.mark.parametrize("status_code", [400, 429, 500, 503])
def test_location_client_reports_http_failure(status_code: int) -> None:
    """Non-authentication HTTP failures should become provider outages."""

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(
            handler=lambda request: httpx.Response(
                status_code,
                request=request,
            )
        )


def test_location_client_reports_network_failure() -> None:
    """Transport details should not escape the provider boundary."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream timeout", request=request)

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=handler)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"name": "London"}),
        httpx.Response(200, json=[{"name": "London"}]),
        httpx.Response(200, json=[create_candidate(lat=91)]),
    ],
)
def test_location_client_reports_invalid_provider_response(
    response: httpx.Response,
) -> None:
    """Malformed provider responses should become safe location errors."""

    with pytest.raises(
        ProviderUnavailableError,
        match="returned an invalid response",
    ):
        run_search(handler=lambda request: response)
