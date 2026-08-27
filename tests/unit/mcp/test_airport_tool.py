"""Tests for the airport-resolution MCP tool."""

import asyncio

from fastmcp import FastMCP

from app.mcp.server import create_mcp_server
from app.mcp.tools.airports import register_airport_tools
from app.providers.airports.schemas import AirportResolution, AirportSearchInput
from app.providers.weather.schemas import CurrentWeather


class FakeAirportResolutionService:
    """Record airport requests and return a configured resolution."""

    def __init__(self, result: AirportResolution) -> None:
        self.result = result
        self.requests: list[AirportSearchInput] = []

    async def resolve_airport(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportResolution:
        """Record and return one deterministic resolution."""

        self.requests.append(request)
        return self.result


class UnusedWeatherProvider:
    """Weather double used only to inspect complete server assembly."""

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Fail if an assembly-only test calls weather."""

        raise AssertionError(f"Unexpected weather request for {city}")


def create_airport_server(service: FakeAirportResolutionService) -> FastMCP:
    """Create an isolated server containing only airport resolution."""

    server = FastMCP(name="Airport tool test")
    register_airport_tools(server, airport_resolution_service=service)
    return server


def test_airport_tool_exposes_bounded_public_schema() -> None:
    """Models should discover a small, token-conscious lookup contract."""

    async def exercise() -> None:
        service = FakeAirportResolutionService(
            AirportResolution(status="resolved", query="LHR", iata_code="LHR")
        )
        tools = await create_airport_server(service).list_tools()

        assert [tool.name for tool in tools] == ["resolve_airport"]
        schema = tools[0].parameters
        assert schema["required"] == ["query"]
        assert schema["properties"]["query"]["minLength"] == 2
        assert schema["properties"]["query"]["maxLength"] == 120
        assert schema["properties"]["max_results"]["minimum"] == 1
        assert schema["properties"]["max_results"]["maximum"] == 5

    asyncio.run(exercise())


def test_airport_tool_normalizes_and_delegates_request() -> None:
    """MCP input should become one validated service request."""

    async def exercise() -> None:
        service = FakeAirportResolutionService(
            AirportResolution(status="resolved", query="London", iata_code="LHR")
        )
        result = await create_airport_server(service).call_tool(
            "resolve_airport",
            {"query": " London ", "max_results": 3},
        )

        assert result.is_error is False
        assert service.requests == [AirportSearchInput(query="London", max_results=3)]
        assert result.structured_content == {
            "status": "resolved",
            "query": "London",
            "iata_code": "LHR",
            "options": [],
        }

    asyncio.run(exercise())


def test_mcp_server_registers_airport_tool_only_when_configured() -> None:
    """An unavailable airport backend must not be advertised to the graph."""

    async def exercise() -> None:
        weather_only = create_mcp_server(weather_provider=UnusedWeatherProvider())
        service = FakeAirportResolutionService(
            AirportResolution(status="resolved", query="LHR", iata_code="LHR")
        )
        complete = create_mcp_server(
            weather_provider=UnusedWeatherProvider(),
            airport_resolution_service=service,
        )

        assert [tool.name for tool in await weather_only.list_tools()] == [
            "get_current_weather"
        ]
        assert [tool.name for tool in await complete.list_tools()] == [
            "get_current_weather",
            "resolve_airport",
        ]

    asyncio.run(exercise())
