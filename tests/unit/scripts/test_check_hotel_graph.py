"""Tests for the live hotel-graph diagnostic script."""

import asyncio
import logging
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

import scripts.check_hotel_graph as script


class FakeAsyncClientContext:
    """Return one fake HTTP client from an async context manager."""

    def __init__(self, http_client: object) -> None:
        self.http_client = http_client
        self.exited = False

    async def __aenter__(self) -> object:
        return self.http_client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


def test_check_hotel_graph_assembles_invokes_and_logs_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The script should wire one hotel tool and log the final response."""

    settings = MagicMock(
        log_level="INFO",
        max_tool_rounds=2,
        duffel_stays_radius_km=25,
    )
    fake_http_client = object()
    http_context = FakeAsyncClientContext(fake_http_client)
    weather_provider = object()
    location_provider = object()
    hotel_provider = object()
    hotel_service = object()
    mcp_server = object()
    mcp_client = object()
    hotel_tool = object()
    model_gateway = object()
    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"assistant_response": "Three verified hotels are available."}
    )
    create_weather_provider = MagicMock(return_value=weather_provider)
    create_location_provider = MagicMock(return_value=location_provider)
    create_hotel_provider = MagicMock(return_value=hotel_provider)
    create_hotel_service = MagicMock(return_value=hotel_service)
    create_server = MagicMock(return_value=mcp_server)
    create_client = MagicMock(return_value=mcp_client)
    create_hotel_tool = MagicMock(return_value=hotel_tool)
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
    monkeypatch.setattr(
        script,
        "WeatherApiLocationClient",
        create_location_provider,
    )
    monkeypatch.setattr(script, "DuffelHotelClient", create_hotel_provider)
    monkeypatch.setattr(script, "HotelSearchService", create_hotel_service)
    monkeypatch.setattr(script, "create_mcp_server", create_server)
    monkeypatch.setattr(script, "TravelMcpClient", create_client)
    monkeypatch.setattr(script, "create_hotel_search_tool", create_hotel_tool)
    monkeypatch.setattr(script, "build_model_gateway", create_gateway)
    monkeypatch.setattr(script, "build_travel_graph", create_graph)

    with caplog.at_level(logging.INFO, logger="scripts.check_hotel_graph"):
        asyncio.run(
            script.check_hotel_graph(
                destination=" London, United Kingdom ",
                check_in_date=date(2026, 9, 10),
                check_out_date=date(2026, 9, 12),
            )
        )

    configure_logging.assert_called_once_with("INFO")
    create_weather_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_location_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_hotel_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_hotel_service.assert_called_once_with(
        location_provider=location_provider,
        hotel_provider=hotel_provider,
        radius_km=25,
    )
    create_server.assert_called_once_with(
        weather_provider=weather_provider,
        hotel_search_service=hotel_service,
    )
    create_client.assert_called_once_with(mcp_server=mcp_server)
    create_hotel_tool.assert_called_once_with(mcp_client=mcp_client)
    create_gateway.assert_called_once_with(settings=settings, tools=[hotel_tool])
    create_graph.assert_called_once_with(
        model_gateway=model_gateway,
        tools=[hotel_tool],
        max_tool_rounds=2,
    )
    graph_input = graph.ainvoke.await_args.args[0]
    assert graph_input["locale"] == "en-PK"
    assert isinstance(graph_input["messages"][0], HumanMessage)
    assert "3 hotels in London, United Kingdom" in graph_input["messages"][0].content
    assert "2026-09-10 to 2026-09-12" in graph_input["messages"][0].content
    assert http_context.exited is True
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Hotel graph check completed"
    )
    assert record.destination == "London, United Kingdom"
    assert record.check_in_date == "2026-09-10"
    assert record.check_out_date == "2026-09-12"
    assert record.assistant_response == "Three verified hotels are available."


def test_check_hotel_graph_rejects_invalid_dates_before_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid stay dates should not spend provider or model resources."""

    get_settings = MagicMock()
    monkeypatch.setattr(script, "get_settings", get_settings)

    with pytest.raises(ValidationError, match="must be after"):
        asyncio.run(
            script.check_hotel_graph(
                destination="London, United Kingdom",
                check_in_date=date(2026, 9, 12),
                check_out_date=date(2026, 9, 10),
            )
        )

    get_settings.assert_not_called()


def test_parse_arguments_returns_typed_hotel_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should parse one destination and two ISO dates."""

    monkeypatch.setattr(
        "sys.argv",
        [
            "check_hotel_graph",
            "London, United Kingdom",
            "2026-09-10",
            "2026-09-12",
        ],
    )

    arguments = script.parse_arguments()

    assert arguments.destination == "London, United Kingdom"
    assert arguments.check_in_date == date(2026, 9, 10)
    assert arguments.check_out_date == date(2026, 9, 12)
