"""Tests for the live flight-graph diagnostic script."""

import asyncio
import logging
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

import scripts.check_flight_graph as script


class FakeAsyncClientContext:
    """Return one fake HTTP client from an async context manager."""

    def __init__(self, http_client: object) -> None:
        self.http_client = http_client
        self.exited = False

    async def __aenter__(self) -> object:
        return self.http_client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


def test_check_flight_graph_assembles_invokes_and_logs_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The script should share ordered tools and log the final graph response."""

    settings = MagicMock(log_level="INFO", max_tool_rounds=2)
    fake_http_client = object()
    http_context = FakeAsyncClientContext(fake_http_client)
    weather_provider = object()
    airport_provider = object()
    airport_resolution_service = object()
    flight_provider = object()
    flight_search_service = object()
    flight_search_preparation_service = object()
    mcp_server = object()
    mcp_client = object()
    weather_tool = object()
    flight_tool = object()
    model_gateway = object()
    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_flights",
                            "args": {
                                "origin": "LHR",
                                "destination": "JFK",
                            },
                            "id": "flight-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "assistant_response": "Three verified offers are available.",
        }
    )
    create_weather_provider = MagicMock(return_value=weather_provider)
    create_airport_provider = MagicMock(return_value=airport_provider)
    create_airport_resolution_service = MagicMock(
        return_value=airport_resolution_service
    )
    create_flight_provider = MagicMock(return_value=flight_provider)
    create_flight_search_service = MagicMock(return_value=flight_search_service)
    create_flight_search_preparation_service = MagicMock(
        return_value=flight_search_preparation_service
    )
    create_server = MagicMock(return_value=mcp_server)
    create_client = MagicMock(return_value=mcp_client)
    create_weather_tool = MagicMock(return_value=weather_tool)
    create_flight_tool = MagicMock(return_value=flight_tool)
    create_gateway = MagicMock(return_value=model_gateway)
    create_graph = MagicMock(return_value=graph)
    configure_logging = MagicMock()
    monkeypatch.setattr(script, "get_settings", MagicMock(return_value=settings))
    monkeypatch.setattr(script, "configure_logging", configure_logging)
    monkeypatch.setattr(
        script.httpx,
        "AsyncClient",
        MagicMock(return_value=http_context),
    )
    monkeypatch.setattr(script, "WeatherApiClient", create_weather_provider)
    monkeypatch.setattr(script, "DuffelAirportClient", create_airport_provider)
    monkeypatch.setattr(
        script,
        "AirportResolutionService",
        create_airport_resolution_service,
    )
    monkeypatch.setattr(script, "DuffelFlightClient", create_flight_provider)
    monkeypatch.setattr(
        script,
        "FlightSearchService",
        create_flight_search_service,
    )
    monkeypatch.setattr(
        script,
        "FlightSearchPreparationService",
        create_flight_search_preparation_service,
    )
    monkeypatch.setattr(script, "create_mcp_server", create_server)
    monkeypatch.setattr(script, "TravelMcpClient", create_client)
    monkeypatch.setattr(script, "create_current_weather_tool", create_weather_tool)
    monkeypatch.setattr(script, "create_flight_search_tool", create_flight_tool)
    monkeypatch.setattr(script, "build_model_gateway", create_gateway)
    monkeypatch.setattr(script, "build_travel_graph", create_graph)

    with caplog.at_level(logging.INFO, logger="scripts.check_flight_graph"):
        asyncio.run(
            script.check_flight_graph(
                origin=" lhr ",
                destination="jfk",
                departure_date=date(2026, 9, 10),
            )
        )

    configure_logging.assert_called_once_with("INFO")
    create_weather_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_flight_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_airport_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_airport_resolution_service.assert_called_once_with(
        airport_provider=airport_provider,
    )
    create_flight_search_service.assert_called_once_with(
        flight_provider=flight_provider,
    )
    create_flight_search_preparation_service.assert_called_once_with(
        airport_resolution_service=airport_resolution_service,
        flight_search_service=flight_search_service,
    )
    create_server.assert_called_once_with(
        weather_provider=weather_provider,
        flight_search_service=flight_search_preparation_service,
    )
    create_client.assert_called_once_with(mcp_server=mcp_server)
    create_weather_tool.assert_called_once_with(mcp_client=mcp_client)
    create_flight_tool.assert_called_once_with(mcp_client=mcp_client)
    tools = [weather_tool, flight_tool]
    create_gateway.assert_called_once_with(settings=settings, tools=tools)
    create_graph.assert_called_once_with(
        model_gateway=model_gateway,
        tools=tools,
        max_tool_rounds=2,
    )
    graph_input = graph.ainvoke.await_args.args[0]
    assert graph_input["locale"] == "en-PK"
    assert isinstance(graph_input["messages"][0], HumanMessage)
    assert "from LHR to JFK" in graph_input["messages"][0].content
    assert "2026-09-10" in graph_input["messages"][0].content
    assert http_context.exited is True
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Flight graph check completed"
    )
    assert record.expected_origin == "LHR"
    assert record.expected_destination == "JFK"
    assert record.model_origin == "LHR"
    assert record.model_destination == "JFK"
    assert record.assistant_response == "Three verified offers are available."


def test_find_flight_tool_arguments_returns_model_selected_route() -> None:
    """The smoke check should expose direction from the actual tool call."""

    messages = [
        HumanMessage(content="lindon se lhaore jana hy"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_flights",
                    "args": {"origin": "London", "destination": "Lahore"},
                    "id": "flight-call",
                    "type": "tool_call",
                }
            ],
        ),
    ]

    assert script.find_flight_tool_arguments(messages) == {
        "origin": "London",
        "destination": "Lahore",
    }


def test_build_graph_message_preserves_raw_roman_urdu_prompt() -> None:
    """A caller should be able to evaluate spelling and direction understanding."""

    request = script.FlightSearchPreparationInput(
        origin="London",
        destination="Lahore",
        departure_date=date(2026, 9, 10),
    )

    assert (
        script.build_graph_message(
            request=request,
            message="  lindon se lhaore jana hy  ",
        )
        == "lindon se lhaore jana hy"
    )


@pytest.mark.parametrize("message", ["   ", "x" * 2001])
def test_build_graph_message_rejects_invalid_raw_prompt(message: str) -> None:
    """Invalid prompts should fail before model or provider resources are created."""

    request = script.FlightSearchPreparationInput(
        origin="London",
        destination="Lahore",
        departure_date=date(2026, 9, 10),
    )

    with pytest.raises(ValueError, match="between 1 and 2000"):
        script.build_graph_message(request=request, message=message)


def test_find_flight_tool_arguments_rejects_missing_flight_call() -> None:
    """A prose-only result should fail rather than appear to verify direction."""

    with pytest.raises(RuntimeError, match="did not call search_flights"):
        script.find_flight_tool_arguments([AIMessage(content="Please provide dates")])


def test_check_flight_graph_rejects_invalid_route_before_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid route input should not spend provider or model resources."""

    get_settings = MagicMock()
    monkeypatch.setattr(script, "get_settings", get_settings)

    with pytest.raises(ValidationError, match="must be different"):
        asyncio.run(
            script.check_flight_graph(
                origin="LHR",
                destination="LHR",
                departure_date=date(2026, 9, 10),
            )
        )

    get_settings.assert_not_called()


def test_parse_arguments_returns_typed_flight_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should parse two IATA codes and an ISO departure date."""

    monkeypatch.setattr(
        "sys.argv",
        ["check_flight_graph", "LHR", "JFK", "2026-09-10"],
    )

    arguments = script.parse_arguments()

    assert arguments.origin == "LHR"
    assert arguments.destination == "JFK"
    assert arguments.departure_date == date(2026, 9, 10)
    assert arguments.message is None


def test_parse_arguments_accepts_raw_natural_language_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should retain a Roman Urdu prompt for live model evaluation."""

    monkeypatch.setattr(
        "sys.argv",
        [
            "check_flight_graph",
            "London",
            "Lahore",
            "2026-09-10",
            "--message",
            "lindon se lhaore jana hy 10 september ko",
        ],
    )

    arguments = script.parse_arguments()

    assert arguments.message == "lindon se lhaore jana hy 10 september ko"
