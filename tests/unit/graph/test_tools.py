"""Tests for LangChain tools backed by the internal MCP client."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.graph.tools import create_current_weather_tool
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


def test_current_weather_tool_exposes_the_bounded_model_schema() -> None:
    """The model should discover one concise and validated city argument."""

    tool = create_current_weather_tool(
        mcp_client=FakeTravelMcpClient(current_weather())
    )

    assert tool.name == "get_current_weather"
    assert "current verified weather" in tool.description
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
