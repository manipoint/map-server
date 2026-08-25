"""Tests for the Frankfurter currency-conversion adapter."""

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import (
    CurrencyPairUnavailableError,
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.providers.currency.frankfurter_client import FrankfurterCurrencyClient
from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)

OBSERVED_AT = datetime(2026, 8, 25, 12, tzinfo=UTC)


def create_settings(**overrides: object) -> Settings:
    """Create isolated settings with Frankfurter enabled."""

    values: dict[str, object] = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
        "currency_provider": "frankfurter",
        "frankfurter_base_url": "https://api.frankfurter.test/v2/",
        "provider_timeout_seconds": 7.0,
    }
    values.update(overrides)
    return Settings(**values)


def create_request(**overrides: object) -> CurrencyConversionInput:
    """Create one valid conversion request with optional overrides."""

    values: dict[str, object] = {
        "amount": "100.25",
        "base_currency": "USD",
        "quote_currency": "PKR",
    }
    values.update(overrides)
    return CurrencyConversionInput(**values)


def create_response_payload(**overrides: object) -> dict[str, object]:
    """Create one valid Frankfurter v2 rate response."""

    values: dict[str, object] = {
        "date": "2026-08-24",
        "base": "USD",
        "quote": "PKR",
        "rate": 278.451234,
    }
    values.update(overrides)
    return values


def run_conversion(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    request: CurrencyConversionInput | None = None,
) -> CurrencyConversionResult:
    """Run one conversion through an in-memory HTTP transport."""

    async def exercise() -> CurrencyConversionResult:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = FrankfurterCurrencyClient(
                http_client=http_client,
                settings=create_settings(),
                clock=lambda: OBSERVED_AT,
            )
            return await client.convert_currency(request=request or create_request())

    return asyncio.run(exercise())


def test_client_rejects_disabled_provider() -> None:
    """Construction should fail when Frankfurter is not enabled."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="not configured"):
                FrankfurterCurrencyClient(
                    http_client=http_client,
                    settings=create_settings(currency_provider=None),
                )

    asyncio.run(exercise())


def test_conversion_requests_one_pair_and_maps_exact_decimals() -> None:
    """The client should fetch only the requested pair and convert with Decimal."""

    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json=create_response_payload(),
            request=request,
        )

    result = run_conversion(handler=handler)

    assert captured_request is not None
    assert str(captured_request.url) == ("https://api.frankfurter.test/v2/rate/USD/PKR")
    assert captured_request.headers["Accept"] == "application/json"
    assert captured_request.extensions["timeout"]["read"] == 7.0
    assert result.amount == Decimal("100.25")
    assert result.rate == Decimal("278.451234")
    assert result.converted_amount == Decimal("27914.736208")
    assert result.rate_date == date(2026, 8, 24)
    assert result.observed_at == OBSERVED_AT


def test_same_currency_conversion_skips_provider_call() -> None:
    """Identity conversion should cost no request and preserve the amount."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500, request=request)

    result = run_conversion(
        handler=handler,
        request=create_request(
            base_currency="USD",
            quote_currency="USD",
        ),
    )

    assert request_count == 0
    assert result.rate == Decimal("1")
    assert result.converted_amount == Decimal("100.25")
    assert result.rate_date == OBSERVED_AT.date()


@pytest.mark.parametrize("status_code", [400, 404, 422])
def test_client_reports_unavailable_currency_pair(status_code: int) -> None:
    """Unsupported currency pairs should not be reported as provider outages."""

    with pytest.raises(CurrencyPairUnavailableError, match="pair is unavailable"):
        run_conversion(
            handler=lambda request: httpx.Response(status_code, request=request)
        )


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
def test_client_reports_provider_http_failure(status_code: int) -> None:
    """Unexpected HTTP failures should become safe provider outages."""

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_conversion(
            handler=lambda request: httpx.Response(status_code, request=request)
        )


def test_client_reports_network_failure() -> None:
    """Transport details must not leak through the provider boundary."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private connection detail", request=request)

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_conversion(handler=handler)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=create_response_payload(rate=0)),
        httpx.Response(200, json=create_response_payload(base="EUR")),
        httpx.Response(200, json={"date": "2026-08-24"}),
    ],
)
def test_client_reports_invalid_provider_response(response: httpx.Response) -> None:
    """Malformed or mismatched rate data should become a safe provider error."""

    def handler(request: httpx.Request) -> httpx.Response:
        response.request = request
        return response

    with pytest.raises(ProviderUnavailableError, match="invalid response"):
        run_conversion(handler=handler)
