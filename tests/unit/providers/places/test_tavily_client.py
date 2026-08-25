"""Tests for the Tavily Search API transport adapter."""

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.providers.places.tavily_client import TavilySearchClient
from app.providers.places.tavily_schemas import TavilyPlaceSearchRequest


def create_settings(**overrides: object) -> Settings:
    """Create isolated settings with Tavily place discovery enabled."""

    values: dict[str, object] = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
        "places_provider": "tavily",
        "tavily_api_key": SecretStr("test-tavily-key"),
        "tavily_search_api_url": "https://api.tavily.test/search",
        "provider_timeout_seconds": 7.0,
    }
    values.update(overrides)
    return Settings(**values)


def create_response_payload(**overrides: object) -> dict[str, object]:
    """Create one minimal valid Tavily search response."""

    values: dict[str, object] = {
        "query": "Best places to visit in London",
        "results": [
            {
                "title": "British Museum",
                "url": "https://www.britishmuseum.org/visit",
                "content": "Visitor information for the British Museum.",
                "score": 0.91,
            }
        ],
        "request_id": "request-123",
    }
    values.update(overrides)
    return values


def run_search(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
) -> object:
    """Run one deterministic Tavily request through an in-memory transport."""

    async def exercise() -> object:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = TavilySearchClient(
                http_client=http_client,
                settings=create_settings(),
            )
            return await client.search(
                request=TavilyPlaceSearchRequest(
                    query="Best places to visit in London",
                    max_results=3,
                )
            )

    return asyncio.run(exercise())


def test_tavily_client_rejects_disabled_provider() -> None:
    """Construction should fail when Tavily place discovery is disabled."""

    async def exercise() -> None:
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="not configured"):
                TavilySearchClient(
                    http_client=http_client,
                    settings=create_settings(
                        places_provider=None,
                        tavily_api_key=None,
                    ),
                )

    asyncio.run(exercise())


def test_tavily_client_rejects_credentials_removed_after_validation() -> None:
    """Construction should defend against credentials removed at runtime."""

    async def exercise() -> None:
        settings = create_settings().model_copy(update={"tavily_api_key": None})
        async with httpx.AsyncClient() as http_client:
            with pytest.raises(ProviderConfigurationError, match="credentials"):
                TavilySearchClient(http_client=http_client, settings=settings)

    asyncio.run(exercise())


def test_tavily_search_sends_auth_and_cost_bounded_json() -> None:
    """The request should authenticate in headers and contain bounded options."""

    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json=create_response_payload(), request=request)

    result = run_search(handler=handler)

    assert captured_request is not None
    assert str(captured_request.url) == "https://api.tavily.test/search"
    assert captured_request.headers["Authorization"] == "Bearer test-tavily-key"
    assert captured_request.headers["Accept"] == "application/json"
    assert captured_request.headers["Content-Type"] == "application/json"
    payload = json.loads(captured_request.content)
    assert payload == {
        "query": "Best places to visit in London",
        "topic": "general",
        "search_depth": "basic",
        "max_results": 3,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "auto_parameters": False,
    }
    assert "test-tavily-key" not in captured_request.content.decode()
    assert result.results[0].title == "British Museum"
    assert result.request_id == "request-123"


@pytest.mark.parametrize("status_code", [401, 403])
def test_tavily_search_reports_rejected_credentials(status_code: int) -> None:
    """Authentication failures should not be reported as provider outages."""

    with pytest.raises(
        ProviderConfigurationError,
        match="credentials were rejected",
    ) as caught:
        run_search(handler=lambda request: httpx.Response(status_code, request=request))

    assert "test-tavily-key" not in str(caught.value)


@pytest.mark.parametrize("status_code", [400, 422, 429, 500, 503])
def test_tavily_search_reports_provider_http_failure(status_code: int) -> None:
    """Non-authentication HTTP failures should become safe outages."""

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=lambda request: httpx.Response(status_code, request=request))


def test_tavily_search_reports_network_failure() -> None:
    """Transport errors should not leak HTTP implementation details."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private connection detail", request=request)

    with pytest.raises(ProviderUnavailableError, match="provider is unavailable"):
        run_search(handler=handler)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"query": "London"}),
        httpx.Response(
            200,
            json=create_response_payload(
                results=[
                    {
                        "title": "Museum",
                        "url": "not-a-url",
                        "content": "Summary",
                        "score": 0.9,
                    }
                ]
            ),
        ),
    ],
)
def test_tavily_search_reports_invalid_provider_response(
    response: httpx.Response,
) -> None:
    """Malformed JSON and schemas should become safe provider failures."""

    with pytest.raises(
        ProviderUnavailableError,
        match="returned an invalid response",
    ):
        run_search(handler=lambda request: response)
