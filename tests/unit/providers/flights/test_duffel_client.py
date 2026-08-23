"""Tests for the Duffel flight-provider HTTP adapter."""

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
from app.domain.flights import FlightSearchStatus
from app.providers.flights.duffel_client import DuffelFlightClient
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult


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


def create_search(**overrides: object) -> FlightSearchInput:
    """Create one valid flight request for client orchestration tests."""

    values: dict[str, object] = {
        "origin": "LHE",
        "destination": "DXB",
        "departure_date": date(2026, 9, 10),
    }
    values.update(overrides)
    return FlightSearchInput(**values)


def create_offer_payload(**overrides: object) -> dict[str, object]:
    """Create a minimal valid Duffel offer response payload."""

    flight_slice = {
        "duration": "PT3H",
        "segments": [
            {
                "origin": {"iata_code": "LHE", "time_zone": "Asia/Karachi"},
                "destination": {"iata_code": "DXB", "time_zone": "Asia/Dubai"},
                "departing_at": "2026-09-10T08:00:00",
                "arriving_at": "2026-09-10T11:00:00",
                "duration": "PT3H",
                "marketing_carrier": {
                    "name": "Example Air",
                    "iata_code": "EX",
                },
                "marketing_carrier_flight_number": "101",
                "operating_carrier": {
                    "name": "Example Air",
                    "iata_code": "EX",
                },
                "operating_carrier_flight_number": "101",
            }
        ],
    }
    values: dict[str, object] = {
        "id": "off_test_123",
        "total_amount": "725.50",
        "total_currency": "USD",
        "expires_at": "2026-09-10T07:30:00Z",
        "slices": [flight_slice],
        "passengers": [{"id": "pas_test_123"}],
    }
    values.update(overrides)
    return values


def run_search(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    request: FlightSearchInput | None = None,
) -> FlightSearchResult:
    """Run one deterministic Duffel search through an in-memory transport."""

    async def exercise() -> FlightSearchResult:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = DuffelFlightClient(
                http_client=http_client,
                settings=create_settings(),
                clock=lambda: datetime(2026, 9, 10, 7, tzinfo=UTC),
            )
            return await client.search_flights(request=request or create_search())

    return asyncio.run(exercise())


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


@pytest.mark.parametrize(
    ("nonstop_only", "expected_max_connections"),
    [
        (False, None),
        (True, "0"),
    ],
)
def test_list_offers_uses_bounded_sorted_query(
    nonstop_only: bool,
    expected_max_connections: str | None,
) -> None:
    """Offer listing should request only the required cheapest results."""

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"data": []})

    async def exercise() -> int:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = DuffelFlightClient(
                http_client=http_client,
                settings=create_settings(),
            )
            response = await client._list_offers(
                offer_request_id="orq_test_123",
                request=FlightSearchInput(
                    origin="LHE",
                    destination="DXB",
                    departure_date=date(2026, 9, 10),
                    nonstop_only=nonstop_only,
                    max_results=3,
                ),
            )
            return len(response.data)

    offer_count = asyncio.run(exercise())

    assert offer_count == 0
    assert len(captured_requests) == 1
    sent_request = captured_requests[0]
    assert sent_request.method == "GET"
    assert str(sent_request.url.copy_with(query=None)) == (
        "https://api.duffel.com/air/offers"
    )
    assert sent_request.url.params["offer_request_id"] == "orq_test_123"
    assert sent_request.url.params["limit"] == "3"
    assert sent_request.url.params["sort"] == "total_amount"
    assert sent_request.url.params.get("max_connections") == (expected_max_connections)


def test_search_flights_orchestrates_requests_and_maps_offer() -> None:
    """The public operation should create, list, and normalize an offer."""

    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"id": "orq_test_123"}})
        return httpx.Response(200, json={"data": [create_offer_payload()]})

    result = run_search(handler=handler)

    assert requested_paths == ["/air/offer_requests", "/air/offers"]
    assert result.status is FlightSearchStatus.OFFERS_AVAILABLE
    assert len(result.offers) == 1
    assert result.offers[0].offer_id == "off_test_123"
    assert result.offers[0].currency == "USD"
    assert result.offers[0].traveler_count == 1
    assert result.searched_at == datetime(2026, 9, 10, 7, tzinfo=UTC)


def test_search_flights_returns_no_offers() -> None:
    """An empty provider page should become a safe availability result."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"id": "orq_test_123"}})
        return httpx.Response(200, json={"data": []})

    result = run_search(handler=handler)

    assert result.status is FlightSearchStatus.NO_OFFERS
    assert result.offers == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_search_flights_reports_rejected_credentials(status_code: int) -> None:
    """Authentication failures should be distinguishable from outages."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"errors": []})

    with pytest.raises(
        ProviderConfigurationError,
        match="credentials were rejected",
    ):
        run_search(handler=handler)


def test_search_flights_hides_non_auth_http_failure() -> None:
    """Provider response details must not leak through a failed status."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="secret Duffel rate-limit detail")

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=handler)


def test_search_flights_normalizes_network_timeout() -> None:
    """Transport timeouts should become the shared provider-unavailable error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret upstream timeout", request=request)

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=handler)


def test_search_flights_rejects_malformed_provider_json() -> None:
    """Invalid provider JSON should become a safe invalid-response failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, content=b"not-json")

    with pytest.raises(ProviderUnavailableError, match="invalid response"):
        run_search(handler=handler)


def test_search_flights_rejects_offer_that_does_not_match_search() -> None:
    """Mapper consistency failures should not escape the provider boundary."""

    offer_payload = create_offer_payload()
    slices = offer_payload["slices"]
    assert isinstance(slices, list)
    offer_payload["slices"] = [slices[0], slices[0]]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"id": "orq_test_123"}})
        return httpx.Response(200, json={"data": [offer_payload]})

    with pytest.raises(ProviderUnavailableError, match="invalid response"):
        run_search(handler=handler)
