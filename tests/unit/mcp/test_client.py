"""Tests for the graph-facing internal MCP client."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.common.exceptions import ProviderUnavailableError
from app.domain.flights import FlightCabinClass, FlightSearchStatus
from app.mcp.client import TravelMcpClient
from app.providers.flights.schemas import FlightSearchInput


class FakeMcpToolServer:
    """In-memory MCP server double with configurable tool behaviour."""

    def __init__(
        self, *, result: object | None = None, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        """Record a tool call and return its configured outcome."""

        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.result


def weather_result(*, is_error: bool = False, content: object | None = None) -> object:
    """Build the minimum MCP result shape consumed by the adapter."""

    return SimpleNamespace(
        is_error=is_error,
        structured_content=content
        if content is not None
        else {
            "location": "Lahore",
            "country": "Pakistan",
            "observed_at": "2026-08-21T12:00:00Z",
            "condition": "Sunny",
            "temperature_c": 35.0,
            "feels_like_c": 37.0,
            "humidity_percent": 40,
            "wind_kph": 12.5,
        },
    )


def flight_result(*, is_error: bool = False, content: object | None = None) -> object:
    """Build the minimum MCP flight result shape consumed by the adapter."""

    return SimpleNamespace(
        is_error=is_error,
        structured_content=content
        if content is not None
        else {
            "status": "no_offers",
            "searched_at": "2026-08-23T12:00:00Z",
            "offers": [],
            "message": "No current flight offers were found.",
        },
    )


def test_get_current_weather_returns_normalized_weather() -> None:
    """The graph client should validate structured MCP output into its domain model."""

    async def exercise() -> None:
        server = FakeMcpToolServer(result=weather_result())
        client = TravelMcpClient(mcp_server=server)

        weather = await client.get_current_weather(city=" Lahore ")

        assert weather.location == "Lahore"
        assert weather.temperature_c == 35.0
        assert server.calls == [
            ("get_current_weather", {"city": "Lahore"}),
        ]

    asyncio.run(exercise())


def test_get_current_weather_hides_mcp_tool_errors() -> None:
    """MCP tool errors should become safe provider-unavailable errors."""

    async def exercise() -> None:
        server = FakeMcpToolServer(result=weather_result(is_error=True))
        client = TravelMcpClient(mcp_server=server)

        with pytest.raises(ProviderUnavailableError, match="tool failed"):
            await client.get_current_weather(city="Lahore")

    asyncio.run(exercise())


def test_get_current_weather_hides_mcp_transport_errors() -> None:
    """MCP transport exceptions should not leak implementation details upward."""

    async def exercise() -> None:
        server = FakeMcpToolServer(error=RuntimeError("connection reset"))
        client = TravelMcpClient(mcp_server=server)

        with pytest.raises(ProviderUnavailableError, match="unavailable"):
            await client.get_current_weather(city="Lahore")

    asyncio.run(exercise())


def test_get_current_weather_rejects_invalid_mcp_content() -> None:
    """Malformed structured content should become a safe provider error."""

    async def exercise() -> None:
        server = FakeMcpToolServer(
            result=weather_result(content={"location": "Lahore"})
        )
        client = TravelMcpClient(mcp_server=server)

        with pytest.raises(ProviderUnavailableError, match="invalid response"):
            await client.get_current_weather(city="Lahore")

    asyncio.run(exercise())


def test_get_current_weather_rejects_blank_city_without_mcp_call() -> None:
    """Invalid graph input should be rejected before an MCP call is attempted."""

    async def exercise() -> None:
        server = FakeMcpToolServer(result=weather_result())
        client = TravelMcpClient(mcp_server=server)

        with pytest.raises(ValidationError):
            await client.get_current_weather(city="   ")

        assert server.calls == []

    asyncio.run(exercise())


def test_search_flights_serializes_request_and_validates_result() -> None:
    """Flight requests should cross MCP as JSON-compatible flat arguments."""

    async def exercise() -> None:
        server = FakeMcpToolServer(result=flight_result())
        client = TravelMcpClient(mcp_server=server)
        request = FlightSearchInput(
            origin="LHE",
            destination="DXB",
            departure_date=date(2026, 9, 10),
            return_date=date(2026, 9, 15),
            adults=1,
            children_ages=[8],
            infants_with_seat_ages=[1],
            cabin_class=FlightCabinClass.BUSINESS,
            currency="PKR",
            max_results=3,
        )

        result = await client.search_flights(request=request)

        assert result.status is FlightSearchStatus.NO_OFFERS
        assert server.calls == [
            (
                "search_flights",
                {
                    "origin": "LHE",
                    "destination": "DXB",
                    "departure_date": "2026-09-10",
                    "return_date": "2026-09-15",
                    "adults": 1,
                    "children_ages": [8],
                    "infants_with_seat_ages": [1],
                    "infants_on_lap_ages": [],
                    "cabin_class": "business",
                    "nonstop_only": False,
                    "currency": "PKR",
                    "max_results": 3,
                },
            )
        ]

    asyncio.run(exercise())


def test_search_flights_hides_mcp_tool_errors() -> None:
    """MCP flight-tool errors should become safe shared provider failures."""

    async def exercise() -> None:
        client = TravelMcpClient(
            mcp_server=FakeMcpToolServer(result=flight_result(is_error=True))
        )

        with pytest.raises(ProviderUnavailableError, match="tool failed"):
            await client.search_flights(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="DXB",
                    departure_date=date(2026, 9, 10),
                )
            )

    asyncio.run(exercise())


def test_search_flights_hides_mcp_transport_errors() -> None:
    """Internal transport details should not escape the graph client."""

    async def exercise() -> None:
        client = TravelMcpClient(
            mcp_server=FakeMcpToolServer(
                error=RuntimeError("secret internal transport detail")
            )
        )

        with pytest.raises(ProviderUnavailableError, match="tool is unavailable"):
            await client.search_flights(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="DXB",
                    departure_date=date(2026, 9, 10),
                )
            )

    asyncio.run(exercise())


def test_search_flights_rejects_invalid_mcp_content() -> None:
    """Malformed structured flight content should become a safe failure."""

    async def exercise() -> None:
        client = TravelMcpClient(
            mcp_server=FakeMcpToolServer(
                result=flight_result(content={"status": "offers_available"})
            )
        )

        with pytest.raises(ProviderUnavailableError, match="invalid response"):
            await client.search_flights(
                request=FlightSearchInput(
                    origin="LHE",
                    destination="DXB",
                    departure_date=date(2026, 9, 10),
                )
            )

    asyncio.run(exercise())
