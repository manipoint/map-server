"""Tests for the live currency-graph diagnostic script."""

import asyncio
import logging
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

import scripts.check_currency_graph as script


class FakeAsyncClientContext:
    """Return one fake HTTP client from an async context manager."""

    def __init__(self, http_client: object) -> None:
        self.http_client = http_client
        self.exited = False

    async def __aenter__(self) -> object:
        return self.http_client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


def test_check_currency_graph_assembles_invokes_and_logs_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The script should share one tool and log only its final graph response."""

    settings = MagicMock(log_level="INFO", max_tool_rounds=2)
    fake_http_client = object()
    http_context = FakeAsyncClientContext(fake_http_client)
    weather_provider = object()
    currency_provider = object()
    mcp_server = object()
    mcp_client = object()
    currency_tool = object()
    model_gateway = object()
    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"assistant_response": "100 USD is 27,845.12 PKR."}
    )
    create_weather_provider = MagicMock(return_value=weather_provider)
    create_currency_provider = MagicMock(return_value=currency_provider)
    create_server = MagicMock(return_value=mcp_server)
    create_client = MagicMock(return_value=mcp_client)
    create_currency_tool = MagicMock(return_value=currency_tool)
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
        "FrankfurterCurrencyClient",
        create_currency_provider,
    )
    monkeypatch.setattr(script, "create_mcp_server", create_server)
    monkeypatch.setattr(script, "TravelMcpClient", create_client)
    monkeypatch.setattr(
        script,
        "create_currency_conversion_tool",
        create_currency_tool,
    )
    monkeypatch.setattr(script, "build_model_gateway", create_gateway)
    monkeypatch.setattr(script, "build_travel_graph", create_graph)

    with caplog.at_level(logging.INFO, logger="scripts.check_currency_graph"):
        asyncio.run(
            script.check_currency_graph(
                amount=Decimal("100.25"),
                base_currency=" usd ",
                quote_currency="pkr",
            )
        )

    configure_logging.assert_called_once_with("INFO")
    create_weather_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_currency_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_server.assert_called_once_with(
        weather_provider=weather_provider,
        currency_provider=currency_provider,
    )
    create_client.assert_called_once_with(mcp_server=mcp_server)
    create_currency_tool.assert_called_once_with(mcp_client=mcp_client)
    create_gateway.assert_called_once_with(settings=settings, tools=[currency_tool])
    create_graph.assert_called_once_with(
        model_gateway=model_gateway,
        tools=[currency_tool],
        max_tool_rounds=2,
    )
    graph_input = graph.ainvoke.await_args.args[0]
    assert graph_input["locale"] == "en-PK"
    assert isinstance(graph_input["messages"][0], HumanMessage)
    assert "100.25 USD to PKR" in graph_input["messages"][0].content
    assert "exactly once" in graph_input["messages"][0].content
    assert http_context.exited is True
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Currency graph check completed"
    )
    assert record.amount == "100.25"
    assert record.base_currency == "USD"
    assert record.quote_currency == "PKR"
    assert record.assistant_response == "100 USD is 27,845.12 PKR."


def test_check_currency_graph_rejects_invalid_input_before_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid amounts should consume neither provider nor model resources."""

    get_settings = MagicMock()
    monkeypatch.setattr(script, "get_settings", get_settings)

    with pytest.raises(ValidationError):
        asyncio.run(
            script.check_currency_graph(
                amount=Decimal("-0.01"),
                base_currency="USD",
                quote_currency="PKR",
            )
        )

    get_settings.assert_not_called()


def test_parse_arguments_returns_decimal_and_currency_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should preserve Decimal precision and both currency codes."""

    monkeypatch.setattr(
        "sys.argv",
        ["check_currency_graph", "100.25", "USD", "PKR"],
    )

    arguments = script.parse_arguments()

    assert arguments.amount == Decimal("100.25")
    assert arguments.base_currency == "USD"
    assert arguments.quote_currency == "PKR"
