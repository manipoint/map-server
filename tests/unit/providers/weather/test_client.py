"""Tests for the normalized WeatherAPI.com provider adapter."""

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
from app.providers.weather.client import WeatherApiClient


def create_settings(**overrides: object) -> Settings:
    """Create valid isolated settings for provider-adapter tests."""

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


def run_weather_request(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    settings: Settings,
    city: str,
):
    """Run one client request with a deterministic in-memory transport."""

    async def exercise():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = WeatherApiClient(http_client=http_client, settings=settings)
            return await client.get_current_weather(city=city)

    return asyncio.run(exercise())


def test_weather_api_client_normalizes_a_successful_response() -> None:
    """Vendor JSON should become a compact UTC normalized weather model."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "location": {"name": "Lahore", "country": "Pakistan"},
                "current": {
                    "last_updated_epoch": 1_786_000_000,
                    "condition": {"text": "Sunny"},
                    "temp_c": 34.5,
                    "feelslike_c": 36.1,
                    "humidity": 42,
                    "wind_kph": 15.3,
                },
            },
        )

    weather = run_weather_request(
        handler=handler,
        settings=create_settings(),
        city="  Lahore  ",
    )

    assert weather.location == "Lahore"
    assert weather.country == "Pakistan"
    assert weather.observed_at.tzinfo is not None
    assert weather.condition == "Sunny"
    assert weather.temperature_c == 34.5
    assert weather.feels_like_c == 36.1
    assert weather.humidity_percent == 42
    assert weather.wind_kph == 15.3
    assert requests[0].url.params["q"] == "Lahore"
    assert requests[0].url.params["aqi"] == "no"


def test_weather_api_client_rejects_a_blank_city_without_http_work() -> None:
    """Invalid city input must not spend an external provider request."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={})

    with pytest.raises(ValueError, match="city must not be blank"):
        run_weather_request(
            handler=handler,
            settings=create_settings(),
            city="   ",
        )

    assert request_count == 0


def test_weather_api_client_rejects_missing_configuration_before_http_work() -> None:
    """A missing API key must fail during client construction."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="not configured"):
                WeatherApiClient(
                    http_client=http_client,
                    settings=create_settings(weather_api_key=None),
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("status_code", [401, 403])
def test_weather_api_client_treats_rejected_credentials_as_configuration_error(
    status_code: int,
) -> None:
    """Rejected credentials require configuration correction, not a retry."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    with pytest.raises(ProviderConfigurationError, match="credentials were rejected"):
        run_weather_request(
            handler=handler,
            settings=create_settings(),
            city="Lahore",
        )


def test_weather_api_client_hides_unavailable_provider_details() -> None:
    """A provider outage should expose only a safe application error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, text="private provider failure")

    with pytest.raises(ProviderUnavailableError) as raised_error:
        run_weather_request(
            handler=handler,
            settings=create_settings(),
            city="Lahore",
        )

    assert str(raised_error.value) == "Weather provider is unavailable"
    assert "private provider failure" not in str(raised_error.value)


def test_weather_api_client_rejects_a_malformed_provider_payload() -> None:
    """Incomplete vendor JSON must not reach graph or MCP layers."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"location": {"name": "Lahore"}})

    with pytest.raises(ProviderUnavailableError, match="invalid response"):
        run_weather_request(
            handler=handler,
            settings=create_settings(),
            city="Lahore",
        )
