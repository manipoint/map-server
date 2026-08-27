"""Tests for graph-facing airport resolution over MCP."""

import asyncio
from types import SimpleNamespace

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.mcp.client import TravelMcpClient
from app.providers.airports.schemas import AirportSearchInput


class FakeMcpServer:
    """Return one configured MCP result while recording tool calls."""

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
        """Record the call and return or raise the configured outcome."""

        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.result


def mcp_result(*, content: object, is_error: bool = False) -> object:
    """Build the minimum MCP result consumed by TravelMcpClient."""

    return SimpleNamespace(is_error=is_error, structured_content=content)


def test_airport_client_validates_and_returns_selection_choices() -> None:
    """A structured ambiguous result should survive the MCP boundary."""

    async def exercise() -> None:
        server = FakeMcpServer(
            result=mcp_result(
                content={
                    "status": "selection_required",
                    "query": "London",
                    "iata_code": None,
                    "options": [
                        {
                            "provider_location_id": "apt_lhr",
                            "iata_code": "LHR",
                            "location_type": "airport",
                            "name": "Heathrow Airport",
                            "city_name": "London",
                            "country_name": "United Kingdom",
                            "country_code": "GB",
                        },
                        {
                            "provider_location_id": "apt_lgw",
                            "iata_code": "LGW",
                            "location_type": "airport",
                            "name": "Gatwick Airport",
                            "city_name": "London",
                            "country_name": "United Kingdom",
                            "country_code": "GB",
                        },
                    ],
                }
            )
        )
        client = TravelMcpClient(mcp_server=server)

        result = await client.resolve_airport(
            request=AirportSearchInput(query="London", max_results=2)
        )

        assert result.status == "selection_required"
        assert [option.iata_code for option in result.options] == ["LHR", "LGW"]
        assert server.calls == [
            ("resolve_airport", {"query": "London", "max_results": 2})
        ]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("server", "message"),
    [
        (
            FakeMcpServer(error=RuntimeError("transport details")),
            "unavailable",
        ),
        (
            FakeMcpServer(result=mcp_result(content={}, is_error=True)),
            "tool failed",
        ),
        (
            FakeMcpServer(result=mcp_result(content={"status": "resolved"})),
            "invalid response",
        ),
    ],
)
def test_airport_client_sanitizes_boundary_failures(
    server: FakeMcpServer,
    message: str,
) -> None:
    """Transport, tool, and payload failures should expose safe errors only."""

    async def exercise() -> None:
        client = TravelMcpClient(mcp_server=server)

        with pytest.raises(ProviderUnavailableError, match=message):
            await client.resolve_airport(request=AirportSearchInput(query="London"))

    asyncio.run(exercise())
