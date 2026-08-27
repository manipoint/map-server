"""Tests for the Duffel airport lookup HTTP adapter."""

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
from app.providers.airports.duffel_client import DuffelAirportClient
from app.providers.airports.schemas import AirportSearchInput, AirportSearchResult


def create_settings(**overrides: object) -> Settings:
    """Create valid isolated settings for Duffel airport-client tests."""

    values: dict[str, object] = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
        "flight_provider": "duffel",
        "duffel_api_key": SecretStr("test-duffel-key"),
    }
    values.update(overrides)
    return Settings(**values)


def create_place_payload(
    *,
    provider_id: str = "arp_lhr_gb",
    iata_code: str = "LHR",
    name: str = "Heathrow",
) -> dict[str, object]:
    """Create one minimal valid Duffel place suggestion payload."""

    return {
        "id": provider_id,
        "iata_code": iata_code,
        "type": "airport",
        "name": name,
        "city_name": "London",
        "iata_country_code": "GB",
    }


def run_search(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    request: AirportSearchInput | None = None,
) -> AirportSearchResult:
    """Run one lookup through an in-memory HTTP transport."""

    async def exercise() -> AirportSearchResult:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = DuffelAirportClient(
                http_client=http_client,
                settings=create_settings(),
            )
            return await client.search_airports(
                request=request or AirportSearchInput(query="London")
            )

    return asyncio.run(exercise())


def test_duffel_airport_client_builds_headers_and_normalized_url() -> None:
    """Every Places request should use shared Duffel authentication metadata."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            client = DuffelAirportClient(
                http_client=http_client,
                settings=create_settings(
                    duffel_base_url="https://api.duffel.com/",
                ),
            )

            assert client.headers == {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Duffel-Version": "v2",
                "Authorization": "Bearer test-duffel-key",
            }
            assert client.suggestions_url == (
                "https://api.duffel.com/places/suggestions"
            )

    asyncio.run(exercise())


def test_duffel_airport_client_rejects_disabled_provider() -> None:
    """Construction should fail before HTTP work when flights are disabled."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="not configured"):
                DuffelAirportClient(
                    http_client=http_client,
                    settings=create_settings(
                        flight_provider=None,
                        duffel_api_key=None,
                    ),
                )

    asyncio.run(exercise())


def test_duffel_airport_client_rejects_missing_credentials() -> None:
    """Construction should reject credentials removed after validation."""

    async def exercise() -> None:
        settings = create_settings()
        settings.duffel_api_key = None

        async with httpx.AsyncClient() as http_client:
            with pytest.raises(
                ProviderConfigurationError,
                match="credentials are missing",
            ):
                DuffelAirportClient(
                    http_client=http_client,
                    settings=settings,
                )

    asyncio.run(exercise())


def test_search_airports_sends_only_query_and_maps_bounded_results() -> None:
    """One HTTP request should return only the requested unique result count."""

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    create_place_payload(),
                    create_place_payload(provider_id="duplicate-lhr"),
                    create_place_payload(
                        provider_id="arp_lgw_gb",
                        iata_code="LGW",
                        name="Gatwick",
                    ),
                    create_place_payload(
                        provider_id="arp_lcy_gb",
                        iata_code="LCY",
                        name="London City",
                    ),
                ]
            },
        )

    result = run_search(
        handler=handler,
        request=AirportSearchInput(query=" London ", max_results=2),
    )

    assert len(captured_requests) == 1
    sent_request = captured_requests[0]
    assert sent_request.method == "GET"
    assert sent_request.url.path == "/places/suggestions"
    assert dict(sent_request.url.params) == {"query": "London"}
    assert sent_request.headers["Duffel-Version"] == "v2"
    assert sent_request.headers["Authorization"] == "Bearer test-duffel-key"
    assert result.query == "London"
    assert [option.iata_code for option in result.options] == ["LHR", "LGW"]


@pytest.mark.parametrize("status_code", [401, 403])
def test_search_airports_maps_rejected_credentials(status_code: int) -> None:
    """Authentication failures should remain configuration errors."""

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


def test_search_airports_maps_other_http_statuses() -> None:
    """Non-authentication HTTP failures should become provider unavailability."""

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(
            handler=lambda request: httpx.Response(
                503,
                request=request,
            )
        )


def test_search_airports_maps_transport_failure() -> None:
    """Connection details should not escape the provider boundary."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret connection detail", request=request)

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=handler)


def test_search_airports_rejects_malformed_provider_response() -> None:
    """Invalid Duffel JSON should become one safe normalized provider failure."""

    with pytest.raises(
        ProviderUnavailableError,
        match="returned an invalid response",
    ):
        run_search(
            handler=lambda request: httpx.Response(
                200,
                json={"data": [{"id": "missing-required-fields"}]},
                request=request,
            )
        )
