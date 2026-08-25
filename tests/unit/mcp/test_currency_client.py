"""Tests for graph-facing MCP currency conversion."""

import asyncio
from types import SimpleNamespace

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.mcp.client import TravelMcpClient
from app.mcp.schemas.currency import CurrencyConversionGuidance
from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)


class FakeMcpToolServer:
    """Record MCP calls and return one configured result."""

    def __init__(
        self,
        *,
        result: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        """Record and execute the configured MCP behavior."""

        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.result


def create_request() -> CurrencyConversionInput:
    """Create one normalized conversion request."""

    return CurrencyConversionInput(
        amount="100.25",
        base_currency="USD",
        quote_currency="PKR",
    )


def mcp_result(*, payload: object, is_error: bool = False) -> object:
    """Create the minimum FastMCP result shape consumed by the client."""

    return SimpleNamespace(
        is_error=is_error,
        structured_content={"result": payload},
    )


def conversion_payload() -> dict[str, object]:
    """Create one valid JSON-compatible normalized conversion."""

    return {
        "amount": "100.25",
        "base_currency": "USD",
        "quote_currency": "PKR",
        "rate": "278.451234",
        "converted_amount": "27914.736208",
        "rate_date": "2026-08-24",
        "observed_at": "2026-08-25T12:00:00Z",
    }


def test_currency_client_serializes_request_and_validates_result() -> None:
    """Currency requests should cross MCP as exact JSON-compatible decimals."""

    async def exercise() -> None:
        server = FakeMcpToolServer(result=mcp_result(payload=conversion_payload()))
        result = await TravelMcpClient(mcp_server=server).convert_currency(
            request=create_request()
        )

        assert isinstance(result, CurrencyConversionResult)
        assert server.calls == [
            (
                "convert_currency",
                {
                    "amount": "100.25",
                    "base_currency": "USD",
                    "quote_currency": "PKR",
                },
            )
        ]

    asyncio.run(exercise())


def test_currency_client_preserves_pair_guidance() -> None:
    """Unsupported-pair guidance should remain typed for the graph."""

    async def exercise() -> None:
        server = FakeMcpToolServer(
            result=mcp_result(
                payload={
                    "status": "pair_unavailable",
                    "message": "Please verify both currency codes.",
                }
            )
        )
        result = await TravelMcpClient(mcp_server=server).convert_currency(
            request=create_request()
        )

        assert isinstance(result, CurrencyConversionGuidance)
        assert result.status == "pair_unavailable"

    asyncio.run(exercise())


def test_currency_client_hides_mcp_tool_errors() -> None:
    """MCP tool failures should become safe provider errors."""

    async def exercise() -> None:
        server = FakeMcpToolServer(
            result=mcp_result(payload={}, is_error=True),
        )

        with pytest.raises(ProviderUnavailableError, match="tool failed"):
            await TravelMcpClient(mcp_server=server).convert_currency(
                request=create_request()
            )

    asyncio.run(exercise())


def test_currency_client_hides_transport_errors() -> None:
    """Internal transport details should not escape the MCP boundary."""

    async def exercise() -> None:
        server = FakeMcpToolServer(error=RuntimeError("private transport detail"))

        with pytest.raises(ProviderUnavailableError, match="tool is unavailable"):
            await TravelMcpClient(mcp_server=server).convert_currency(
                request=create_request()
            )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "structured_content",
    [
        {},
        {"result": {"amount": "100.25"}},
        {"result": conversion_payload() | {"rate": "0"}},
    ],
)
def test_currency_client_rejects_invalid_mcp_content(
    structured_content: dict[str, object],
) -> None:
    """Malformed currency output should become a safe provider failure."""

    async def exercise() -> None:
        server = FakeMcpToolServer(
            result=SimpleNamespace(
                is_error=False,
                structured_content=structured_content,
            )
        )

        with pytest.raises(ProviderUnavailableError, match="invalid response"):
            await TravelMcpClient(mcp_server=server).convert_currency(
                request=create_request()
            )

    asyncio.run(exercise())
