"""Tests for the normalized current-weather MCP tool."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.mcp.schemas.weather import CurrentWeatherInput
from app.mcp.server import create_mcp_server
from app.providers.weather.schemas import CurrentWeather


class FakeWeatherProvider:
    """Deterministic weather provider double for MCP contract tests."""

    def __init__(self) -> None:
        self.cities: list[str] = []

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Record the normalized city and return vendor-independent weather."""

        self.cities.append(city)
        return CurrentWeather(
            location=city,
            country="Pakistan",
            observed_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
            condition="Sunny",
            temperature_c=35.0,
            feels_like_c=37.0,
            humidity_percent=40,
            wind_kph=12.5,
        )


def test_current_weather_input_enforces_a_bounded_city_name() -> None:
    """The MCP boundary should reject blank or unbounded city input."""

    assert CurrentWeatherInput(city=" Lahore ").city == "Lahore"

    with pytest.raises(ValidationError):
        CurrentWeatherInput(city="")
    with pytest.raises(ValidationError):
        CurrentWeatherInput(city="   ")
    with pytest.raises(ValidationError):
        CurrentWeatherInput(city="x" * 121)


def test_weather_mcp_tool_exposes_a_bounded_city_schema() -> None:
    """LLM clients should discover only the validated public tool contract."""

    async def exercise() -> None:
        server = create_mcp_server(weather_provider=FakeWeatherProvider())
        tools = await server.list_tools()

        assert [tool.name for tool in tools] == ["get_current_weather"]
        schema = tools[0].parameters
        assert schema["required"] == ["city"]
        assert schema["properties"]["city"] == {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        }

    asyncio.run(exercise())


def test_weather_mcp_tool_returns_only_normalized_structured_weather() -> None:
    """The MCP tool should call its injected provider with the validated city."""

    async def exercise() -> None:
        provider = FakeWeatherProvider()
        server = create_mcp_server(weather_provider=provider)

        result = await server.call_tool(
            "get_current_weather",
            {"city": "Lahore"},
        )

        assert result.is_error is False
        assert provider.cities == ["Lahore"]
        assert result.structured_content == {
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
