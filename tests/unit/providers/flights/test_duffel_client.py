"""Tests for the Duffel flight-provider HTTP adapter."""

import asyncio
import json
from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import ProviderConfigurationError
from app.config import Settings
from app.providers.flights.duffel_client import DuffelFlightClient
from app.providers.flights.schemas import FlightSearchInput


def create_settings(**overrides: object) -> Settings:
    """Create valid isolated settings for Duffel client tests."""

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


def test_duffel_client_builds_required_headers_and_normalized_urls() -> None:
    """Every Duffel request should use auth, versioning, and stable endpoints."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            client = DuffelFlightClient(
                http_client=http_client,
                settings=create_settings(duffel_base_url="https://api.duffel.com/"),
            )

            assert client.headers == {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Duffel-Version": "v2",
                "Authorization": "Bearer test-duffel-key",
            }
            assert client.offer_requests_url == (
                "https://api.duffel.com/air/offer_requests"
            )
            assert client.offers_url == "https://api.duffel.com/air/offers"

    asyncio.run(exercise())


def test_duffel_client_rejects_a_disabled_provider() -> None:
    """Construction should fail before HTTP work when Duffel is disabled."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="not configured"):
                DuffelFlightClient(
                    http_client=http_client,
                    settings=create_settings(
                        flight_provider=None,
                        duffel_api_key=None,
                    ),
                )

    asyncio.run(exercise())


def test_create_offer_request_sends_bounded_search_and_returns_id() -> None:
    """The first HTTP call should omit offers and return its request reference."""

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(201, json={"data": {"id": "orq_test_123"}})

    async def exercise() -> str:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = DuffelFlightClient(
                http_client=http_client,
                settings=create_settings(duffel_supplier_timeout_ms=12_000),
            )
            return await client._create_offer_request(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="DXB",
                    departure_date=date(2026, 9, 10),
                    adults=2,
                    children_ages=[8],
                )
            )

    offer_request_id = asyncio.run(exercise())

    assert offer_request_id == "orq_test_123"
    assert len(captured_requests) == 1
    sent_request = captured_requests[0]
    assert sent_request.method == "POST"
    assert str(sent_request.url.copy_with(query=None)) == (
        "https://api.duffel.com/air/offer_requests"
    )
    assert sent_request.url.params["return_offers"] == "false"
    assert sent_request.url.params["supplier_timeout"] == "12000"
    assert sent_request.headers["Duffel-Version"] == "v2"
    assert sent_request.headers["Authorization"] == "Bearer test-duffel-key"
    assert json.loads(sent_request.content) == {
        "data": {
            "slices": [
                {
                    "origin": "LHE",
                    "destination": "DXB",
                    "departure_date": "2026-09-10",
                }
            ],
            "passengers": [
                {"type": "adult"},
                {"type": "adult"},
                {"age": 8},
            ],
            "cabin_class": "economy",
        }
    }
