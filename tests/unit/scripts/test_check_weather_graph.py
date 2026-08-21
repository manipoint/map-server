"""Tests for the live weather-graph diagnostic script."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

import scripts.check_weather_graph as script


class FakeAsyncClientContext:
    """Return one fake HTTP client from an async context manager."""

    def __init__(self, http_client: object) -> None:
        self.http_client = http_client
        self.exited = False

    async def __aenter__(self) -> object:
        return self.http_client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


def test_check_weather_graph_assembles_and_invokes_the_live_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The script should share one tool and print the graph's final response."""

    settings = MagicMock()
    settings.max_tool_rounds = 2
    fake_http_client = object()
    http_context = FakeAsyncClientContext(fake_http_client)
    weather_provider = object()
    mcp_server = object()
    mcp_client = object()
    weather_tool = object()
    model_gateway = object()
    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"assistant_response": "Lahore is sunny at 35°C."}
    )
    create_http_client = MagicMock(return_value=http_context)
    create_weather_provider = MagicMock(return_value=weather_provider)
    create_server = MagicMock(return_value=mcp_server)
    create_client = MagicMock(return_value=mcp_client)
    create_tool = MagicMock(return_value=weather_tool)
    create_gateway = MagicMock(return_value=model_gateway)
    create_graph = MagicMock(return_value=graph)
    monkeypatch.setattr(script, "get_settings", MagicMock(return_value=settings))
    monkeypatch.setattr(script.httpx, "AsyncClient", create_http_client)
    monkeypatch.setattr(script, "WeatherApiClient", create_weather_provider)
    monkeypatch.setattr(script, "create_mcp_server", create_server)
    monkeypatch.setattr(script, "TravelMcpClient", create_client)
    monkeypatch.setattr(script, "create_current_weather_tool", create_tool)
    monkeypatch.setattr(script, "build_model_gateway", create_gateway)
    monkeypatch.setattr(script, "build_travel_graph", create_graph)

    asyncio.run(script.check_weather_graph(" Lahore "))

    create_http_client.assert_called_once_with()
    create_weather_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_server.assert_called_once_with(weather_provider=weather_provider)
    create_client.assert_called_once_with(mcp_server=mcp_server)
    create_tool.assert_called_once_with(mcp_client=mcp_client)
    create_gateway.assert_called_once_with(
        settings=settings,
        tools=[weather_tool],
    )
    create_graph.assert_called_once_with(
        model_gateway=model_gateway,
        tools=[weather_tool],
        max_tool_rounds=2,
    )
    graph_input = graph.ainvoke.await_args.args[0]
    assert graph_input["locale"] == "en-PK"
    assert len(graph_input["messages"]) == 1
    assert isinstance(graph_input["messages"][0], HumanMessage)
    assert graph_input["messages"][0].content == (
        "What is the current weather in Lahore?"
    )
    assert http_context.exited is True
    assert capsys.readouterr().out == "Lahore is sunny at 35°C.\n"


def test_check_weather_graph_rejects_blank_city_before_creating_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid input should not spend provider or model resources."""

    get_settings = MagicMock()
    monkeypatch.setattr(script, "get_settings", get_settings)

    with pytest.raises(ValidationError):
        asyncio.run(script.check_weather_graph("   "))

    get_settings.assert_not_called()


def test_parse_arguments_requires_and_returns_the_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should expose one required positional city argument."""

    monkeypatch.setattr(
        "sys.argv",
        ["check_weather_graph", "Chishtian"],
    )

    arguments = script.parse_arguments()

    assert arguments.city == "Chishtian"
