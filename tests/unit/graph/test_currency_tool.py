"""Tests for the model-facing currency-conversion tool."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.graph.tools import create_currency_conversion_tool
from app.mcp.schemas.currency import CurrencyConversionGuidance
from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)


def conversion_result() -> CurrencyConversionResult:
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


class FakeCurrencyMcpClient:
    """Record normalized requests and return one configured response."""

    def __init__(
        self,
        result: CurrencyConversionResult | CurrencyConversionGuidance | BaseException,
    ) -> None:
        self.result = result
        self.requests: list[CurrencyConversionInput] = []

    async def convert_currency(
        self,
        *,
        request: CurrencyConversionInput,
    ) -> CurrencyConversionResult | CurrencyConversionGuidance:
        """Record the request before returning or raising."""

        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_currency_tool_exposes_normalized_model_schema() -> None:
    """The model should discover exact amount and currency-code arguments."""

    tool = create_currency_conversion_tool(
        mcp_client=FakeCurrencyMcpClient(conversion_result())
    )

    assert tool.name == "convert_currency"
    assert "verified reference exchange rate" in tool.description
    assert "not a payment" in tool.description
    assert tool.args_schema is CurrencyConversionInput
    schema = tool.args_schema.model_json_schema()
    assert schema["required"] == ["amount", "base_currency", "quote_currency"]


def test_currency_tool_normalizes_request_and_returns_json() -> None:
    """Model arguments should become one normalized MCP request."""

    async def exercise() -> None:
        mcp_client = FakeCurrencyMcpClient(conversion_result())
        tool = create_currency_conversion_tool(mcp_client=mcp_client)

        result = await tool.ainvoke(
            {
                "amount": "100.25",
                "base_currency": " usd ",
                "quote_currency": " pkr ",
            }
        )

        assert len(mcp_client.requests) == 1
        request = mcp_client.requests[0]
        assert request.amount == Decimal("100.25")
        assert request.base_currency == "USD"
        assert request.quote_currency == "PKR"
        assert result == {
            "amount": "100.25",
            "base_currency": "USD",
            "quote_currency": "PKR",
            "rate": "278.451234",
            "converted_amount": "27914.736208",
            "rate_date": "2026-08-24",
            "observed_at": "2026-08-25T12:00:00Z",
        }

    asyncio.run(exercise())


def test_currency_tool_returns_pair_guidance_as_json() -> None:
    """The model should receive actionable unsupported-pair guidance."""

    async def exercise() -> None:
        guidance = CurrencyConversionGuidance(
            message="Please verify both currency codes."
        )
        tool = create_currency_conversion_tool(
            mcp_client=FakeCurrencyMcpClient(guidance)
        )

        result = await tool.ainvoke(
            {
                "amount": "10",
                "base_currency": "AAA",
                "quote_currency": "USD",
            }
        )

        assert result == guidance.model_dump(mode="json")

    asyncio.run(exercise())


def test_currency_tool_rejects_invalid_amount_before_mcp_call() -> None:
    """Invalid model arguments should spend no MCP or provider request."""

    async def exercise() -> None:
        mcp_client = FakeCurrencyMcpClient(conversion_result())
        tool = create_currency_conversion_tool(mcp_client=mcp_client)

        with pytest.raises(ValueError):
            await tool.ainvoke(
                {
                    "amount": "-1",
                    "base_currency": "USD",
                    "quote_currency": "PKR",
                }
            )

        assert mcp_client.requests == []

    asyncio.run(exercise())


def test_currency_tool_propagates_safe_provider_errors() -> None:
    """The graph should retain the MCP client's sanitized failure."""

    async def exercise() -> None:
        tool = create_currency_conversion_tool(
            mcp_client=FakeCurrencyMcpClient(
                ProviderUnavailableError("Currency-conversion tool failed")
            )
        )

        with pytest.raises(ProviderUnavailableError, match="tool failed"):
            await tool.ainvoke(
                {
                    "amount": "10",
                    "base_currency": "USD",
                    "quote_currency": "PKR",
                }
            )

    asyncio.run(exercise())
