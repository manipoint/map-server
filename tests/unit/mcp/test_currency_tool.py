"""Tests for the normalized currency-conversion MCP tool."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from fastmcp import FastMCP

from app.common.exceptions import CurrencyPairUnavailableError
from app.mcp.server import create_mcp_server
from app.mcp.tools.currency import register_currency_tools
from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)
from app.providers.weather.schemas import CurrentWeather


def create_conversion_result() -> CurrencyConversionResult:
    """Create one deterministic normalized conversion."""

    return CurrencyConversionResult(
        amount="100.25",
        base_currency="USD",
        quote_currency="PKR",
        rate="278.451234",
        converted_amount="27914.736208",
        rate_date=date(2026, 8, 24),
        observed_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
    )


class FakeCurrencyProvider:
    """Record conversion requests and return a configured outcome."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[CurrencyConversionInput] = []

    async def convert_currency(
        self,
        *,
        request: CurrencyConversionInput,
    ) -> CurrencyConversionResult:
        """Record one request before returning or raising."""

        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return create_conversion_result()


class UnusedWeatherProvider:
    """Weather double required by complete MCP server assembly."""

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Fail if an assembly test unexpectedly invokes weather."""

        raise AssertionError(f"Unexpected weather request for {city}")


def create_server(provider: FakeCurrencyProvider) -> FastMCP:
    """Create an isolated MCP server containing only the currency tool."""

    server = FastMCP(name="Currency tool test")
    register_currency_tools(server, currency_provider=provider)
    return server


def test_currency_tool_exposes_validated_public_schema() -> None:
    """Clients should discover amount bounds and normalized currency codes."""

    async def exercise() -> None:
        tools = await create_server(FakeCurrencyProvider()).list_tools()

        assert [tool.name for tool in tools] == ["convert_currency"]
        schema = tools[0].parameters
        assert schema["required"] == [
            "amount",
            "base_currency",
            "quote_currency",
        ]
        properties = schema["properties"]
        assert properties["amount"]["anyOf"][0]["minimum"] == 0
        for field_name in ("base_currency", "quote_currency"):
            assert properties[field_name]["minLength"] == 3
            assert properties[field_name]["maxLength"] == 3
            assert properties[field_name]["pattern"] == "^[A-Z]{3}$"

    asyncio.run(exercise())


def test_mcp_server_registers_currency_only_when_provider_is_available() -> None:
    """Currency conversion should only be advertised when configured."""

    async def exercise() -> None:
        weather_only = create_mcp_server(weather_provider=UnusedWeatherProvider())
        with_currency = create_mcp_server(
            weather_provider=UnusedWeatherProvider(),
            currency_provider=FakeCurrencyProvider(),
        )

        assert [tool.name for tool in await weather_only.list_tools()] == [
            "get_current_weather"
        ]
        assert [tool.name for tool in await with_currency.list_tools()] == [
            "get_current_weather",
            "convert_currency",
        ]

    asyncio.run(exercise())


def test_currency_tool_normalizes_and_returns_structured_conversion() -> None:
    """MCP should delegate one normalized request and return no provider payload."""

    async def exercise() -> None:
        provider = FakeCurrencyProvider()
        result = await create_server(provider).call_tool(
            "convert_currency",
            {
                "amount": "100.25",
                "base_currency": " usd ",
                "quote_currency": " pkr ",
            },
        )

        assert result.is_error is False
        assert len(provider.requests) == 1
        request = provider.requests[0]
        assert request.amount == Decimal("100.25")
        assert request.base_currency == "USD"
        assert request.quote_currency == "PKR"
        assert result.structured_content == {
            "result": {
                "amount": "100.25",
                "base_currency": "USD",
                "quote_currency": "PKR",
                "rate": "278.451234",
                "converted_amount": "27914.736208",
                "rate_date": "2026-08-24",
                "observed_at": "2026-08-25T12:00:00Z",
            }
        }

    asyncio.run(exercise())


def test_currency_tool_returns_pair_guidance_without_mcp_error() -> None:
    """An unsupported pair should remain user-correctable structured guidance."""

    async def exercise() -> None:
        provider = FakeCurrencyProvider(
            error=CurrencyPairUnavailableError("unsupported pair")
        )
        result = await create_server(provider).call_tool(
            "convert_currency",
            {
                "amount": "10",
                "base_currency": "AAA",
                "quote_currency": "USD",
            },
        )

        assert result.is_error is False
        assert result.structured_content == {
            "result": {
                "status": "pair_unavailable",
                "message": (
                    "No reference rate is available for that currency pair. "
                    "Please verify both three-letter currency codes."
                ),
            }
        }

    asyncio.run(exercise())
