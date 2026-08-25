"""Tests for the normalized place-search MCP tool."""

import asyncio
from datetime import UTC, datetime

from fastmcp import FastMCP

from app.common.exceptions import AmbiguousLocationError, LocationNotFoundError
from app.domain.places import PlaceSearchStatus
from app.mcp.server import create_mcp_server
from app.mcp.tools.places import register_place_tools
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.schemas import PlaceSearchInput, PlaceSearchResult
from app.providers.weather.schemas import CurrentWeather


def create_location() -> ResolvedLocation:
    """Create one resolved London location for tool results."""

    return ResolvedLocation(
        query="London",
        display_name="London, United Kingdom",
        latitude=51.5071,
        longitude=-0.1276,
    )


def create_result() -> PlaceSearchResult:
    """Create one normalized no-places response."""

    return PlaceSearchResult(
        status=PlaceSearchStatus.NO_PLACES,
        searched_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
        location=create_location(),
        message="No relevant places were found for this search.",
    )


class FakePlaceSearchService:
    """Deterministic place service double for MCP contract tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[PlaceSearchInput] = []

    async def search_places(
        self,
        *,
        request: PlaceSearchInput,
    ) -> PlaceSearchResult:
        """Record a request and return or raise the configured outcome."""

        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return create_result()


class UnusedWeatherProvider:
    """Weather double required by the complete MCP server factory."""

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Fail if an assembly test unexpectedly invokes weather."""

        raise AssertionError(f"Unexpected weather request for {city}")


def create_server(service: FakePlaceSearchService) -> FastMCP:
    """Create an isolated MCP server containing only the places tool."""

    server = FastMCP(name="Place tool test")
    register_place_tools(server, place_search_service=service)  # type: ignore[arg-type]
    return server


def test_place_tool_exposes_cost_bounded_public_schema() -> None:
    """LLM clients should discover exact preference and result limits."""

    async def exercise() -> None:
        tools = await create_server(FakePlaceSearchService()).list_tools()

        assert [tool.name for tool in tools] == ["search_places"]
        schema = tools[0].parameters
        assert schema["required"] == ["destination"]
        properties = schema["properties"]
        assert properties["destination"]["minLength"] == 2
        assert properties["destination"]["maxLength"] == 120
        assert properties["interests"]["anyOf"][0]["maxItems"] == 10
        assert properties["max_results"]["minimum"] == 1
        assert properties["max_results"]["maximum"] == 5

    asyncio.run(exercise())


def test_mcp_server_registers_places_only_when_service_is_available() -> None:
    """Place discovery should only be advertised with its complete service."""

    async def exercise() -> None:
        weather_only = create_mcp_server(weather_provider=UnusedWeatherProvider())
        with_places = create_mcp_server(
            weather_provider=UnusedWeatherProvider(),
            place_search_service=FakePlaceSearchService(),  # type: ignore[arg-type]
        )

        assert [tool.name for tool in await weather_only.list_tools()] == [
            "get_current_weather"
        ]
        assert [tool.name for tool in await with_places.list_tools()] == [
            "get_current_weather",
            "search_places",
        ]

    asyncio.run(exercise())


def test_place_tool_preserves_interests_and_family_preference() -> None:
    """User discovery preferences should reach the service once."""

    async def exercise() -> None:
        service = FakePlaceSearchService()
        result = await create_server(service).call_tool(
            "search_places",
            {
                "destination": "London, United Kingdom",
                "interests": ["museums", "parks", "museums"],
                "family_friendly": True,
                "max_results": 3,
            },
        )

        assert result.is_error is False
        assert len(service.requests) == 1
        request = service.requests[0]
        assert request.destination == "London, United Kingdom"
        assert request.interests == ["museums", "parks"]
        assert request.family_friendly is True
        assert request.max_results == 3

    asyncio.run(exercise())


def test_place_tool_returns_normalized_search_result() -> None:
    """Successful service output should remain structured across MCP."""

    async def exercise() -> None:
        result = await create_server(FakePlaceSearchService()).call_tool(
            "search_places",
            {"destination": "London, United Kingdom"},
        )

        assert result.is_error is False
        assert result.structured_content == {
            "result": {
                "status": "no_places",
                "searched_at": "2026-08-25T12:00:00Z",
                "location": {
                    "query": "London",
                    "display_name": "London, United Kingdom",
                    "latitude": 51.5071,
                    "longitude": -0.1276,
                },
                "places": [],
                "message": "No relevant places were found for this search.",
            }
        }

    asyncio.run(exercise())


def test_place_tool_returns_ambiguity_candidates() -> None:
    """Ambiguous destinations should become actionable guidance."""

    async def exercise() -> None:
        service = FakePlaceSearchService(
            error=AmbiguousLocationError(
                candidates=[
                    "London, United Kingdom",
                    "London, Ontario, Canada",
                ]
            )
        )
        result = await create_server(service).call_tool(
            "search_places",
            {"destination": "London"},
        )

        assert result.is_error is False
        assert result.structured_content == {
            "result": {
                "status": "location_ambiguous",
                "message": (
                    "Multiple destinations matched. Please select one location."
                ),
                "candidates": [
                    "London, United Kingdom",
                    "London, Ontario, Canada",
                ],
            }
        }

    asyncio.run(exercise())


def test_place_tool_returns_not_found_guidance() -> None:
    """A missing destination should remain a user-correctable outcome."""

    async def exercise() -> None:
        service = FakePlaceSearchService(
            error=LocationNotFoundError("No location matched")
        )
        result = await create_server(service).call_tool(
            "search_places",
            {"destination": "Unknown"},
        )

        assert result.is_error is False
        assert result.structured_content == {
            "result": {
                "status": "location_not_found",
                "message": "No matching destination was found.",
                "candidates": [],
            }
        }

    asyncio.run(exercise())
