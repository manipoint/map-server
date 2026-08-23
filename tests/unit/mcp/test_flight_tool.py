"""Tests for the normalized flight-search MCP tool."""

import asyncio
from datetime import UTC, date, datetime

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from app.domain.flights import FlightCabinClass, FlightSearchStatus
from app.mcp.server import create_mcp_server
from app.mcp.tools.flights import register_flight_tools
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult
from app.providers.weather.schemas import CurrentWeather


class FakeFlightProvider:
    """Deterministic provider double for flight-tool contract tests."""

    def __init__(self) -> None:
        self.requests: list[FlightSearchInput] = []

    async def search_flights(
        self,
        *,
        request: FlightSearchInput,
    ) -> FlightSearchResult:
        """Record the normalized request and return a safe group outcome."""

        self.requests.append(request)
        return FlightSearchResult(
            status=FlightSearchStatus.GROUP_BOOKING_REQUIRED,
            searched_at=datetime(2026, 8, 22, 12, tzinfo=UTC),
            message=(
                "Contact the airline group desk for one quote covering all travelers."
            ),
        )


class UnusedWeatherProvider:
    """Weather provider double needed only for complete server assembly."""

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Fail if an assembly-only test unexpectedly executes weather."""

        raise AssertionError(f"Unexpected weather request for {city}")


def create_server(provider: FakeFlightProvider) -> FastMCP:
    """Create an isolated MCP server containing only the flight tool."""

    server = FastMCP(name="Flight tool test")
    register_flight_tools(server, flight_provider=provider)
    return server


def test_flight_tool_exposes_bounded_public_schema() -> None:
    """LLM clients should discover required fields and bounded result controls."""

    async def exercise() -> None:
        server = create_server(FakeFlightProvider())
        tools = await server.list_tools()

        assert [tool.name for tool in tools] == ["search_flights"]
        schema = tools[0].parameters
        assert schema["required"] == [
            "origin",
            "destination",
            "departure_date",
        ]
        assert schema["properties"]["origin"]["minLength"] == 3
        assert schema["properties"]["origin"]["maxLength"] == 3
        assert schema["properties"]["max_results"]["minimum"] == 1
        assert schema["properties"]["max_results"]["maximum"] == 10
        assert "children_ages" in schema["properties"]
        assert "infants_with_seat_ages" in schema["properties"]
        assert "infants_on_lap_ages" in schema["properties"]
        assert "children" not in schema["properties"]

    asyncio.run(exercise())


def test_flight_tool_preserves_child_and_twin_ages() -> None:
    """A single parent should be able to search for a child and seated/lap twins."""

    async def exercise() -> None:
        provider = FakeFlightProvider()
        server = create_server(provider)

        result = await server.call_tool(
            "search_flights",
            {
                "origin": "LHE",
                "destination": "DXB",
                "departure_date": "2026-09-10",
                "adults": 1,
                "children_ages": [8],
                "infants_with_seat_ages": [1],
                "infants_on_lap_ages": [1],
            },
        )

        assert result.is_error is False
        request = provider.requests[0]
        assert request.children_ages == [8]
        assert request.infants_with_seat_ages == [1]
        assert request.infants_on_lap_ages == [1]
        assert request.total_travelers == 4

    asyncio.run(exercise())


def test_mcp_server_registers_flight_tool_only_when_provider_is_available() -> None:
    """Flight search should not be advertised without a configured provider."""

    async def exercise() -> None:
        weather_only = create_mcp_server(weather_provider=UnusedWeatherProvider())
        complete = create_mcp_server(
            weather_provider=UnusedWeatherProvider(),
            flight_provider=FakeFlightProvider(),
        )

        assert [tool.name for tool in await weather_only.list_tools()] == [
            "get_current_weather"
        ]
        assert [tool.name for tool in await complete.list_tools()] == [
            "get_current_weather",
            "search_flights",
        ]

    asyncio.run(exercise())


def test_flight_tool_normalizes_and_delegates_real_world_group_search() -> None:
    """The tool should accept large groups and pass one normalized request."""

    async def exercise() -> None:
        provider = FakeFlightProvider()
        server = create_server(provider)

        result = await server.call_tool(
            "search_flights",
            {
                "origin": "lhe",
                "destination": "khi",
                "departure_date": "2026-09-10",
                "adults": 12,
                "currency": "pkr",
                "cabin_class": "business",
            },
        )

        assert result.is_error is False
        assert len(provider.requests) == 1
        request = provider.requests[0]
        assert request.origin == "LHE"
        assert request.destination == "KHI"
        assert request.departure_date == date(2026, 9, 10)
        assert request.adults == 12
        assert request.currency == "PKR"
        assert request.cabin_class is FlightCabinClass.BUSINESS

    asyncio.run(exercise())


def test_flight_tool_returns_group_booking_as_structured_outcome() -> None:
    """Provider booking limits should not become generic MCP failures."""

    async def exercise() -> None:
        server = create_server(FakeFlightProvider())

        result = await server.call_tool(
            "search_flights",
            {
                "origin": "LHE",
                "destination": "KHI",
                "departure_date": "2026-09-10",
                "adults": 12,
            },
        )

        assert result.is_error is False
        assert result.structured_content == {
            "status": "group_booking_required",
            "searched_at": "2026-08-22T12:00:00Z",
            "offers": [],
            "message": (
                "Contact the airline group desk for one quote covering all travelers."
            ),
        }

    asyncio.run(exercise())


def test_flight_tool_rejects_excess_lap_infants_before_provider_call() -> None:
    """Invalid infant supervision should fail without calling the provider."""

    async def exercise() -> None:
        provider = FakeFlightProvider()
        server = create_server(provider)

        with pytest.raises(
            ValidationError,
            match="book additional infants with their own seat",
        ):
            await server.call_tool(
                "search_flights",
                {
                    "origin": "LHE",
                    "destination": "KHI",
                    "departure_date": "2026-09-10",
                    "adults": 1,
                    "infants_on_lap_ages": [0, 1],
                },
            )

        assert provider.requests == []

    asyncio.run(exercise())
