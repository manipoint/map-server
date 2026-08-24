"""Tests for the normalized hotel-search MCP tool."""

import asyncio
from datetime import UTC, datetime

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from app.common.exceptions import (
    AmbiguousLocationError,
    InvalidTravelDateError,
    LocationNotFoundError,
)
from app.domain.hotels import HotelSearchStatus
from app.mcp.server import create_mcp_server
from app.mcp.tools.hotels import register_hotel_tools
from app.providers.hotels.schemas import HotelSearchInput, HotelSearchResult
from app.providers.locations.schemas import ResolvedLocation
from app.providers.weather.schemas import CurrentWeather


def create_location() -> ResolvedLocation:
    """Create one resolved London location for tool results."""

    return ResolvedLocation(
        query="London",
        display_name="London, United Kingdom",
        latitude=51.5071,
        longitude=-0.1276,
    )


def create_result() -> HotelSearchResult:
    """Create one normalized no-availability response."""

    return HotelSearchResult(
        status=HotelSearchStatus.NO_HOTELS,
        searched_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        location=create_location(),
        message="No current hotels were found for this search.",
    )


class FakeHotelSearchService:
    """Deterministic hotel service double for MCP contract tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[HotelSearchInput] = []

    async def search_hotels(
        self,
        *,
        request: HotelSearchInput,
    ) -> HotelSearchResult:
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


def create_server(service: FakeHotelSearchService) -> FastMCP:
    """Create an isolated MCP server containing only the hotel tool."""

    server = FastMCP(name="Hotel tool test")
    register_hotel_tools(server, hotel_search_service=service)  # type: ignore[arg-type]
    return server


def test_hotel_tool_exposes_bounded_public_schema() -> None:
    """LLM clients should discover exact dates, party fields, and limits."""

    async def exercise() -> None:
        tools = await create_server(FakeHotelSearchService()).list_tools()

        assert [tool.name for tool in tools] == ["search_hotels"]
        schema = tools[0].parameters
        assert schema["required"] == [
            "destination",
            "check_in_date",
            "check_out_date",
        ]
        properties = schema["properties"]
        assert properties["destination"]["minLength"] == 2
        assert properties["destination"]["maxLength"] == 120
        assert properties["max_results"]["minimum"] == 1
        assert properties["max_results"]["maximum"] == 10
        assert properties["children_ages"]["anyOf"][0]["items"]["minimum"] == 0
        assert properties["children_ages"]["anyOf"][0]["items"]["maximum"] == 17

    asyncio.run(exercise())


def test_mcp_server_registers_hotel_tool_only_when_service_is_available() -> None:
    """Hotel search should only be advertised with its complete service."""

    async def exercise() -> None:
        weather_only = create_mcp_server(weather_provider=UnusedWeatherProvider())
        with_hotels = create_mcp_server(
            weather_provider=UnusedWeatherProvider(),
            hotel_search_service=FakeHotelSearchService(),  # type: ignore[arg-type]
        )

        assert [tool.name for tool in await weather_only.list_tools()] == [
            "get_current_weather"
        ]
        assert [tool.name for tool in await with_hotels.list_tools()] == [
            "get_current_weather",
            "search_hotels",
        ]

    asyncio.run(exercise())


def test_hotel_tool_preserves_family_rooms_and_preferences() -> None:
    """A single parent and twins should reach the service without group limits."""

    async def exercise() -> None:
        service = FakeHotelSearchService()
        result = await create_server(service).call_tool(
            "search_hotels",
            {
                "destination": "London, United Kingdom",
                "check_in_date": "2026-09-10",
                "check_out_date": "2026-09-12",
                "adults": 1,
                "children_ages": [4, 8],
                "rooms": 1,
                "free_cancellation_only": True,
                "max_results": 3,
            },
        )

        assert result.is_error is False
        request = service.requests[0]
        assert request.destination == "London, United Kingdom"
        assert request.children_ages == [4, 8]
        assert request.total_guests == 3
        assert request.rooms == 1
        assert request.free_cancellation_only is True
        assert request.max_results == 3

    asyncio.run(exercise())


def test_hotel_tool_returns_normalized_search_result() -> None:
    """Successful service output should remain structured across MCP."""

    async def exercise() -> None:
        result = await create_server(FakeHotelSearchService()).call_tool(
            "search_hotels",
            {
                "destination": "London, United Kingdom",
                "check_in_date": "2026-09-10",
                "check_out_date": "2026-09-12",
            },
        )

        assert result.is_error is False
        assert result.structured_content == {
            "result": {
                "status": "no_hotels",
                "searched_at": "2026-08-24T12:00:00Z",
                "location": {
                    "query": "London",
                    "display_name": "London, United Kingdom",
                    "latitude": 51.5071,
                    "longitude": -0.1276,
                },
                "options": [],
                "message": "No current hotels were found for this search.",
            },
        }

    asyncio.run(exercise())


def test_hotel_tool_returns_ambiguity_candidates() -> None:
    """Ambiguous destinations should become actionable structured guidance."""

    async def exercise() -> None:
        service = FakeHotelSearchService(
            error=AmbiguousLocationError(
                candidates=[
                    "London, United Kingdom",
                    "London, Ontario, Canada",
                ]
            )
        )
        result = await create_server(service).call_tool(
            "search_hotels",
            {
                "destination": "London",
                "check_in_date": "2026-09-10",
                "check_out_date": "2026-09-12",
            },
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


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            LocationNotFoundError("No location matched"),
            {
                "status": "location_not_found",
                "message": "No matching destination was found.",
                "candidates": [],
            },
        ),
        (
            InvalidTravelDateError("Hotel check-in date cannot be in the past"),
            {
                "status": "invalid_dates",
                "message": "Hotel check-in date cannot be in the past",
                "candidates": [],
            },
        ),
    ],
)
def test_hotel_tool_returns_non_provider_guidance(
    error: Exception,
    expected: dict[str, object],
) -> None:
    """Expected user-correctable errors should not become MCP failures."""

    async def exercise() -> None:
        result = await create_server(FakeHotelSearchService(error=error)).call_tool(
            "search_hotels",
            {
                "destination": "Unknown",
                "check_in_date": "2026-09-10",
                "check_out_date": "2026-09-12",
            },
        )

        assert result.is_error is False
        assert result.structured_content == {"result": expected}

    asyncio.run(exercise())


def test_hotel_tool_rejects_invalid_party_before_service_call() -> None:
    """Rooms without enough adults should fail without provider work."""

    async def exercise() -> None:
        service = FakeHotelSearchService()

        with pytest.raises(ValidationError, match="each room requires"):
            await create_server(service).call_tool(
                "search_hotels",
                {
                    "destination": "London, United Kingdom",
                    "check_in_date": "2026-09-10",
                    "check_out_date": "2026-09-12",
                    "adults": 1,
                    "rooms": 2,
                },
            )

        assert service.requests == []

    asyncio.run(exercise())
