"""Tests for the Duffel hotel-provider HTTP adapter."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.domain.hotels import HotelSearchStatus
from app.providers.hotels.duffel_client import DuffelHotelClient
from app.providers.hotels.schemas import (
    HotelSearchInput,
    HotelSearchResult,
    ResolvedHotelSearch,
)
from app.providers.locations.schemas import ResolvedLocation


def create_settings(**overrides: object) -> Settings:
    """Create valid isolated settings for Duffel hotel-client tests."""

    values: dict[str, object] = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
        "hotel_provider": "duffel",
        "duffel_api_key": SecretStr("test-duffel-key"),
    }
    values.update(overrides)
    return Settings(**values)


def create_search(**overrides: object) -> ResolvedHotelSearch:
    """Create one provider-ready London hotel search."""

    request_values: dict[str, object] = {
        "destination": "London",
        "check_in_date": date(2026, 9, 10),
        "check_out_date": date(2026, 9, 12),
    }
    request_values.update(overrides)
    return ResolvedHotelSearch(
        request=HotelSearchInput(**request_values),
        location=ResolvedLocation(
            query="London",
            display_name="London, United Kingdom",
            latitude=51.5071,
            longitude=-0.1416,
        ),
        radius_km=5,
    )


def create_result_payload(**overrides: object) -> dict[str, object]:
    """Create one minimal valid Duffel Stays search result."""

    values: dict[str, object] = {
        "id": "srr_test_123",
        "check_in_date": "2026-09-10",
        "check_out_date": "2026-09-12",
        "rooms": 1,
        "expires_at": "2026-09-01T12:00:00Z",
        "cheapest_rate_total_amount": "799.00",
        "cheapest_rate_currency": "GBP",
        "accommodation": {
            "id": "acc_test_123",
            "name": "Example Hotel",
            "location": {
                "address": {
                    "city_name": "London",
                    "country_code": "GB",
                },
                "geographic_coordinates": {
                    "latitude": 51.5071,
                    "longitude": -0.1416,
                },
            },
        },
    }
    values.update(overrides)
    return values


def create_response_payload(
    results: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Create one top-level Duffel Stays response."""

    return {
        "data": {
            "created_at": "2026-08-24T12:00:00Z",
            "results": [create_result_payload()] if results is None else results,
        }
    }


def run_search(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    search: ResolvedHotelSearch | None = None,
) -> HotelSearchResult:
    """Run one deterministic hotel search through an in-memory transport."""

    async def exercise() -> HotelSearchResult:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = DuffelHotelClient(
                http_client=http_client,
                settings=create_settings(),
                clock=lambda: datetime(2026, 8, 24, 12, tzinfo=UTC),
            )
            return await client.search_hotels(search=search or create_search())

    return asyncio.run(exercise())


def test_duffel_hotel_client_builds_headers_and_normalized_url() -> None:
    """Every Stays request should use auth, versioning, and a stable endpoint."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            client = DuffelHotelClient(
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
            assert client.search_url == "https://api.duffel.com/stays/search"

    asyncio.run(exercise())


def test_duffel_hotel_client_rejects_disabled_provider() -> None:
    """Construction should fail before HTTP work when Stays is disabled."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="not configured"):
                DuffelHotelClient(
                    http_client=http_client,
                    settings=create_settings(
                        hotel_provider=None,
                        duffel_api_key=None,
                    ),
                )

    asyncio.run(exercise())


def test_duffel_hotel_client_rejects_missing_credentials() -> None:
    """Construction should reject credentials removed after configuration."""

    async def exercise() -> None:
        settings = create_settings()
        settings.duffel_api_key = None

        async with httpx.AsyncClient() as http_client:
            with pytest.raises(
                ProviderConfigurationError,
                match="credentials are missing",
            ):
                DuffelHotelClient(
                    http_client=http_client,
                    settings=settings,
                )

    asyncio.run(exercise())


def test_search_hotels_sends_payload_and_maps_response() -> None:
    """The public operation should send exact criteria and normalize a result."""

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=create_response_payload())

    result = run_search(
        handler=handler,
        search=create_search(
            adults=1,
            children_ages=[8],
            free_cancellation_only=True,
        ),
    )

    assert len(captured_requests) == 1
    sent_request = captured_requests[0]
    assert sent_request.method == "POST"
    assert sent_request.url.path == "/stays/search"
    assert sent_request.headers["Duffel-Version"] == "v2"
    assert sent_request.headers["Authorization"] == "Bearer test-duffel-key"
    assert json.loads(sent_request.content) == {
        "data": {
            "location": {
                "radius": 5,
                "geographic_coordinates": {
                    "latitude": 51.5071,
                    "longitude": -0.1416,
                },
            },
            "check_in_date": "2026-09-10",
            "check_out_date": "2026-09-12",
            "guests": [
                {"type": "adult"},
                {"type": "child", "age": 8},
            ],
            "rooms": 1,
            "free_cancellation_only": True,
            "mobile": True,
        }
    }
    assert result.status is HotelSearchStatus.HOTELS_AVAILABLE
    assert len(result.options) == 1
    assert result.options[0].search_result_id == "srr_test_123"
    assert result.options[0].guest_count == 2


def test_search_hotels_returns_no_hotels() -> None:
    """An empty provider result list should become safe no availability."""

    result = run_search(
        handler=lambda request: httpx.Response(
            200,
            json=create_response_payload(results=[]),
        )
    )

    assert result.status is HotelSearchStatus.NO_HOTELS
    assert result.options == []


def test_search_hotels_reports_rejected_credentials() -> None:
    """Invalid credentials should be distinguishable from outages."""

    with pytest.raises(
        ProviderConfigurationError,
        match="credentials were rejected",
    ):
        run_search(
            handler=lambda request: httpx.Response(
                401,
                request=request,
            )
        )


def test_search_hotels_reports_and_logs_denied_stays_access(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Forbidden Stays access should retain only safe support diagnostics."""

    with (
        caplog.at_level(
            "WARNING",
            logger="app.providers.hotels.duffel_client",
        ),
        pytest.raises(
            ProviderConfigurationError,
            match="Stays access was denied",
        ),
    ):
        run_search(
            handler=lambda request: httpx.Response(
                403,
                headers={"x-request-id": "req_test_stays_123"},
                request=request,
            )
        )

    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Duffel hotel request failed"
    )
    assert record.status_code == 403
    assert record.duffel_request_id == "req_test_stays_123"
    assert "test-duffel-key" not in caplog.text
    assert "Authorization" not in caplog.text


@pytest.mark.parametrize("status_code", [422, 429, 500, 503])
def test_search_hotels_reports_provider_http_failure(status_code: int) -> None:
    """Non-authentication HTTP failures should become provider outages."""

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(
            handler=lambda request: httpx.Response(
                status_code,
                request=request,
            )
        )


def test_search_hotels_reports_network_failure() -> None:
    """Transport failures should not leak HTTP implementation details."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=handler)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"data": {"results": []}}),
        httpx.Response(200, json={"data": {"created_at": "invalid"}}),
    ],
)
def test_search_hotels_reports_invalid_provider_response(
    response: httpx.Response,
) -> None:
    """Malformed JSON and schemas should become safe provider errors."""

    with pytest.raises(
        ProviderUnavailableError,
        match="returned an invalid response",
    ):
        run_search(handler=lambda request: response)
