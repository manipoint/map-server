"""Tests for LangChain tools backed by the internal MCP client."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.domain.flights import FlightCabinClass, FlightSearchStatus
from app.domain.hotels import HotelSearchStatus
from app.domain.places import PlaceSearchStatus
from app.graph.tools import (
    create_current_weather_tool,
    create_flight_search_tool,
    create_hotel_search_tool,
    create_place_search_tool,
)
from app.mcp.schemas.flights import FlightSearchPreparationInput
from app.mcp.schemas.hotels import HotelSearchGuidance
from app.mcp.schemas.places import PlaceSearchGuidance
from app.providers.flights.schemas import FlightSearchResult
from app.providers.hotels.schemas import HotelSearchInput, HotelSearchResult
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.schemas import PlaceSearchInput, PlaceSearchResult
from app.providers.weather.schemas import CurrentWeather


class FakeTravelMcpClient:
    """Record weather requests and return a configured result."""

    def __init__(self, result: CurrentWeather | BaseException) -> None:
        self.result = result
        self.cities: list[str] = []

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Record the city before returning or raising the configured result."""

        self.cities.append(city)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeFlightMcpClient:
    """Record normalized flight requests and return a configured result."""

    def __init__(self, result: FlightSearchResult | BaseException) -> None:
        self.result = result
        self.requests: list[FlightSearchPreparationInput] = []

    async def search_flights(
        self,
        *,
        request: FlightSearchPreparationInput,
    ) -> FlightSearchResult:
        """Record the flight request before returning or raising."""

        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeHotelMcpClient:
    """Record normalized hotel requests and return a configured response."""

    def __init__(
        self,
        result: HotelSearchResult | HotelSearchGuidance | BaseException,
    ) -> None:
        self.result = result
        self.requests: list[HotelSearchInput] = []

    async def search_hotels(
        self,
        *,
        request: HotelSearchInput,
    ) -> HotelSearchResult | HotelSearchGuidance:
        """Record the hotel request before returning or raising."""

        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakePlaceMcpClient:
    """Record normalized place requests and return a configured response."""

    def __init__(
        self,
        result: PlaceSearchResult | PlaceSearchGuidance | BaseException,
    ) -> None:
        self.result = result
        self.requests: list[PlaceSearchInput] = []

    async def search_places(
        self,
        *,
        request: PlaceSearchInput,
    ) -> PlaceSearchResult | PlaceSearchGuidance:
        """Record the place request before returning or raising."""

        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def current_weather() -> CurrentWeather:
    """Build deterministic normalized weather for tool tests."""

    return CurrentWeather(
        location="Lahore",
        country="Pakistan",
        observed_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        condition="Sunny",
        temperature_c=35.0,
        feels_like_c=37.0,
        humidity_percent=40,
        wind_kph=12.5,
    )


def no_flight_offers() -> FlightSearchResult:
    """Build a deterministic normalized empty flight result."""

    return FlightSearchResult(
        status=FlightSearchStatus.NO_OFFERS,
        searched_at=datetime(2026, 8, 23, 12, tzinfo=UTC),
        message="No current flight offers were found.",
    )


def no_hotel_options() -> HotelSearchResult:
    """Build a deterministic normalized empty hotel result."""

    return HotelSearchResult(
        status=HotelSearchStatus.NO_HOTELS,
        searched_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        location=ResolvedLocation(
            query="London, United Kingdom",
            display_name="London, United Kingdom",
            latitude=51.5071,
            longitude=-0.1276,
        ),
        message="No current hotels were found.",
    )


def no_place_options() -> PlaceSearchResult:
    """Build a deterministic normalized empty place result."""

    return PlaceSearchResult(
        status=PlaceSearchStatus.NO_PLACES,
        searched_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
        location=ResolvedLocation(
            query="London, United Kingdom",
            display_name="London, United Kingdom",
            latitude=51.5071,
            longitude=-0.1276,
        ),
        message="No relevant places were found.",
    )


def test_current_weather_tool_exposes_the_bounded_model_schema() -> None:
    """The model should discover one concise and validated city argument."""

    tool = create_current_weather_tool(
        mcp_client=FakeTravelMcpClient(current_weather())
    )

    assert tool.name == "get_current_weather"
    assert "verified current conditions" in tool.description
    assert "not a forecast" in tool.description
    assert tool.args_schema is not None
    schema = tool.args_schema.model_json_schema()
    assert schema["required"] == ["city"]
    assert schema["properties"]["city"] == {
        "title": "City",
        "type": "string",
        "minLength": 1,
        "maxLength": 120,
    }


def test_current_weather_tool_returns_json_safe_normalized_data() -> None:
    """Tool execution should trim input and return no provider-specific payload."""

    async def exercise() -> None:
        mcp_client = FakeTravelMcpClient(current_weather())
        tool = create_current_weather_tool(mcp_client=mcp_client)

        result = await tool.ainvoke({"city": " Lahore "})

        assert mcp_client.cities == ["Lahore"]
        assert result == {
            "location": "Lahore",
            "country": "Pakistan",
            "observed_at": "2026-08-21T12:00:00Z",
            "condition": "Sunny",
            "temperature_c": 35.0,
            "feels_like_c": 37.0,
            "humidity_percent": 40,
            "wind_kph": 12.5,
        }

    asyncio.run(exercise())


def test_current_weather_tool_propagates_safe_provider_errors() -> None:
    """The graph layer should retain the adapter's sanitized provider failure."""

    async def exercise() -> None:
        mcp_client = FakeTravelMcpClient(
            ProviderUnavailableError("Current-weather tool failed")
        )
        tool = create_current_weather_tool(mcp_client=mcp_client)

        with pytest.raises(ProviderUnavailableError, match="tool failed"):
            await tool.ainvoke({"city": "Lahore"})

    asyncio.run(exercise())


def test_flight_search_tool_exposes_bounded_model_schema() -> None:
    """The model should discover the normalized passenger and result controls."""

    tool = create_flight_search_tool(mcp_client=FakeFlightMcpClient(no_flight_offers()))

    assert tool.name == "search_flights"
    assert "exact child ages" in tool.description
    assert "no booking" in tool.description
    assert tool.args_schema is FlightSearchPreparationInput
    schema = tool.args_schema.model_json_schema()
    assert schema["required"] == ["origin", "destination", "departure_date"]
    assert schema["properties"]["max_results"]["minimum"] == 1
    assert schema["properties"]["max_results"]["maximum"] == 10
    assert "children_ages" in schema["properties"]
    assert "infants_with_seat_ages" in schema["properties"]
    assert "infants_on_lap_ages" in schema["properties"]


def test_flight_search_tool_normalizes_family_request_and_returns_json() -> None:
    """Flat model arguments should become one validated provider-independent request."""

    async def exercise() -> None:
        mcp_client = FakeFlightMcpClient(no_flight_offers())
        tool = create_flight_search_tool(mcp_client=mcp_client)

        result = await tool.ainvoke(
            {
                "origin": "lhe",
                "destination": "dxb",
                "departure_date": "2026-09-10",
                "return_date": "2026-09-15",
                "adults": 1,
                "children_ages": [8],
                "infants_with_seat_ages": [1],
                "infants_on_lap_ages": [1],
                "cabin_class": "business",
                "currency": "pkr",
                "max_results": 3,
            }
        )

        request = mcp_client.requests[0]
        assert request.origin == "LHE"
        assert request.destination == "DXB"
        assert request.cabin_class is FlightCabinClass.BUSINESS
        assert request.currency == "PKR"
        assert request.total_travelers == 4
        assert result == {
            "status": "no_offers",
            "searched_at": "2026-08-23T12:00:00Z",
            "offers": [],
            "message": "No current flight offers were found.",
        }

    asyncio.run(exercise())


def test_flight_search_tool_rejects_invalid_family_before_mcp_call() -> None:
    """More lap infants than adults should not spend a provider request."""

    async def exercise() -> None:
        mcp_client = FakeFlightMcpClient(no_flight_offers())
        tool = create_flight_search_tool(mcp_client=mcp_client)

        with pytest.raises(ValueError, match="each lap infant"):
            await tool.ainvoke(
                {
                    "origin": "LHE",
                    "destination": "DXB",
                    "departure_date": "2026-09-10",
                    "adults": 1,
                    "infants_on_lap_ages": [0, 1],
                }
            )

        assert mcp_client.requests == []

    asyncio.run(exercise())


def test_flight_search_tool_propagates_safe_provider_errors() -> None:
    """The graph tool should preserve the MCP client's sanitized failure."""

    async def exercise() -> None:
        tool = create_flight_search_tool(
            mcp_client=FakeFlightMcpClient(
                ProviderUnavailableError("Flight-search tool failed")
            )
        )

        with pytest.raises(ProviderUnavailableError, match="tool failed"):
            await tool.ainvoke(
                {
                    "origin": "LHE",
                    "destination": "DXB",
                    "departure_date": "2026-09-10",
                }
            )

    asyncio.run(exercise())


def test_hotel_search_tool_exposes_bounded_model_schema() -> None:
    """The model should discover dates, rooms, family ages, and result bounds."""

    tool = create_hotel_search_tool(mcp_client=FakeHotelMcpClient(no_hotel_options()))

    assert tool.name == "search_hotels"
    assert "every child's exact age" in tool.description
    assert "no booking" in tool.description
    assert tool.args_schema is HotelSearchInput
    schema = tool.args_schema.model_json_schema()
    assert schema["required"] == [
        "destination",
        "check_in_date",
        "check_out_date",
    ]
    assert schema["properties"]["max_results"]["minimum"] == 1
    assert schema["properties"]["max_results"]["maximum"] == 10
    assert "children_ages" in schema["properties"]


def test_hotel_search_tool_normalizes_family_request_and_returns_json() -> None:
    """Model arguments should become one validated provider-independent request."""

    async def exercise() -> None:
        mcp_client = FakeHotelMcpClient(no_hotel_options())
        tool = create_hotel_search_tool(mcp_client=mcp_client)

        result = await tool.ainvoke(
            {
                "destination": " London, United Kingdom ",
                "check_in_date": "2026-09-10",
                "check_out_date": "2026-09-12",
                "adults": 1,
                "children_ages": [4, 8],
                "rooms": 1,
                "free_cancellation_only": True,
                "max_results": 3,
            }
        )

        request = mcp_client.requests[0]
        assert request.destination == "London, United Kingdom"
        assert request.total_guests == 3
        assert request.free_cancellation_only is True
        assert result["status"] == "no_hotels"
        assert result["searched_at"] == "2026-08-24T12:00:00Z"

    asyncio.run(exercise())


def test_hotel_search_tool_returns_location_guidance_as_json() -> None:
    """The model should receive actionable candidates for ambiguous locations."""

    async def exercise() -> None:
        guidance = HotelSearchGuidance(
            status="location_ambiguous",
            message="Please select one location.",
            candidates=[
                "London, United Kingdom",
                "London, Ontario, Canada",
            ],
        )
        tool = create_hotel_search_tool(mcp_client=FakeHotelMcpClient(guidance))

        result = await tool.ainvoke(
            {
                "destination": "London",
                "check_in_date": "2026-09-10",
                "check_out_date": "2026-09-12",
            }
        )

        assert result == guidance.model_dump(mode="json")

    asyncio.run(exercise())


def test_hotel_search_tool_rejects_invalid_rooms_before_mcp_call() -> None:
    """Every room should have an adult before spending an MCP request."""

    async def exercise() -> None:
        mcp_client = FakeHotelMcpClient(no_hotel_options())
        tool = create_hotel_search_tool(mcp_client=mcp_client)

        with pytest.raises(ValueError, match="each room requires"):
            await tool.ainvoke(
                {
                    "destination": "London, United Kingdom",
                    "check_in_date": "2026-09-10",
                    "check_out_date": "2026-09-12",
                    "adults": 1,
                    "rooms": 2,
                }
            )

        assert mcp_client.requests == []

    asyncio.run(exercise())


def test_hotel_search_tool_propagates_safe_provider_errors() -> None:
    """The graph tool should preserve the MCP client's sanitized failure."""

    async def exercise() -> None:
        tool = create_hotel_search_tool(
            mcp_client=FakeHotelMcpClient(
                ProviderUnavailableError("Hotel-search tool failed")
            )
        )

        with pytest.raises(ProviderUnavailableError, match="tool failed"):
            await tool.ainvoke(
                {
                    "destination": "London, United Kingdom",
                    "check_in_date": "2026-09-10",
                    "check_out_date": "2026-09-12",
                }
            )

    asyncio.run(exercise())


def test_place_search_tool_exposes_cost_bounded_model_schema() -> None:
    """The model should discover interests and the five-result limit."""

    tool = create_place_search_tool(mcp_client=FakePlaceMcpClient(no_place_options()))

    assert tool.name == "search_places"
    assert "verified attractions" in tool.description
    assert "at most five" in tool.description
    assert tool.args_schema is PlaceSearchInput
    schema = tool.args_schema.model_json_schema()
    assert schema["required"] == ["destination"]
    assert schema["properties"]["interests"]["maxItems"] == 10
    assert schema["properties"]["max_results"]["minimum"] == 1
    assert schema["properties"]["max_results"]["maximum"] == 5


def test_place_search_tool_normalizes_preferences_and_returns_json() -> None:
    """Model arguments should become one normalized place request."""

    async def exercise() -> None:
        mcp_client = FakePlaceMcpClient(no_place_options())
        tool = create_place_search_tool(mcp_client=mcp_client)

        result = await tool.ainvoke(
            {
                "destination": " London, United Kingdom ",
                "interests": ["Museums", "parks", "museums"],
                "family_friendly": True,
                "max_results": 3,
            }
        )

        request = mcp_client.requests[0]
        assert request.destination == "London, United Kingdom"
        assert request.interests == ["Museums", "parks"]
        assert request.family_friendly is True
        assert request.max_results == 3
        assert result["status"] == "no_places"
        assert result["searched_at"] == "2026-08-25T12:00:00Z"

    asyncio.run(exercise())


def test_place_search_tool_returns_location_guidance_as_json() -> None:
    """The model should receive actionable ambiguous-location candidates."""

    async def exercise() -> None:
        guidance = PlaceSearchGuidance(
            status="location_ambiguous",
            message="Please select one location.",
            candidates=[
                "London, United Kingdom",
                "London, Ontario, Canada",
            ],
        )
        tool = create_place_search_tool(mcp_client=FakePlaceMcpClient(guidance))

        result = await tool.ainvoke({"destination": "London"})

        assert result == guidance.model_dump(mode="json")

    asyncio.run(exercise())


def test_place_search_tool_rejects_excess_results_before_mcp_call() -> None:
    """An oversized request should consume no MCP or provider request."""

    async def exercise() -> None:
        mcp_client = FakePlaceMcpClient(no_place_options())
        tool = create_place_search_tool(mcp_client=mcp_client)

        with pytest.raises(ValueError):
            await tool.ainvoke(
                {
                    "destination": "London, United Kingdom",
                    "max_results": 6,
                }
            )

        assert mcp_client.requests == []

    asyncio.run(exercise())


def test_place_search_tool_propagates_safe_provider_errors() -> None:
    """The graph tool should preserve the MCP client's sanitized failure."""

    async def exercise() -> None:
        tool = create_place_search_tool(
            mcp_client=FakePlaceMcpClient(
                ProviderUnavailableError("Place-search tool failed")
            )
        )

        with pytest.raises(ProviderUnavailableError, match="tool failed"):
            await tool.ainvoke({"destination": "London, United Kingdom"})

    asyncio.run(exercise())
