"""Tests for travel graph construction."""

import asyncio
from collections.abc import Sequence

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool

from app.graph.builder import build_travel_graph
from app.graph.exceptions import ToolRoundLimitError


class FakeModelGateway:
    """Deterministic gateway double for graph tests."""

    def __init__(self, responses: Sequence[AIMessage]) -> None:
        self.responses = list(responses)
        self.calls: list[list[BaseMessage]] = []

    async def generate(self, *, messages: Sequence[BaseMessage]) -> AIMessage:
        """Record input and return a fixed assistant reply."""

        self.calls.append(list(messages))
        return self.responses[len(self.calls) - 1]


def create_weather_tool(cities: list[str] | None = None) -> StructuredTool:
    """Create a deterministic async weather tool for graph tests."""

    async def get_current_weather(city: str) -> dict[str, object]:
        if cities is not None:
            cities.append(city)
        return {
            "location": city,
            "condition": "Sunny",
            "temperature_c": 35.0,
        }

    return StructuredTool.from_function(
        coroutine=get_current_weather,
        name="get_current_weather",
        description="Return current verified weather for a city.",
    )


def weather_tool_call(call_number: int) -> AIMessage:
    """Build a model request for the weather tool."""

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_current_weather",
                "args": {"city": "Lahore"},
                "id": f"weather-call-{call_number}",
                "type": "tool_call",
            }
        ],
    )


def test_travel_graph_generates_a_final_assistant_response() -> None:
    """Graph should invoke the gateway and build the final text response."""

    gateway = FakeModelGateway([AIMessage(content="Here is your Lahore itinerary.")])
    graph = build_travel_graph(
        model_gateway=gateway,
        tools=[create_weather_tool()],
        max_tool_rounds=2,
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="Plan Lahore trip")],
                "locale": "en-PK",
            }
        )
    )

    assert result["assistant_response"] == "Here is your Lahore itinerary."
    assert len(gateway.calls) == 1
    assert isinstance(gateway.calls[0][0], SystemMessage)
    assert gateway.calls[0][1].content == "Plan Lahore trip"
    assert len(result["messages"]) == 2
    assert all(not isinstance(message, SystemMessage) for message in result["messages"])
    assert result["messages"][-1].content == "Here is your Lahore itinerary."


def test_travel_graph_executes_weather_and_returns_a_model_summary() -> None:
    """A tool request should execute once before the model writes final text."""

    cities: list[str] = []
    gateway = FakeModelGateway(
        [
            weather_tool_call(1),
            AIMessage(content="Lahore is sunny at 35°C."),
        ]
    )
    graph = build_travel_graph(
        model_gateway=gateway,
        tools=[create_weather_tool(cities)],
        max_tool_rounds=2,
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="Weather in Lahore?")],
                "locale": "en-PK",
            }
        )
    )

    assert result["assistant_response"] == "Lahore is sunny at 35°C."
    assert result["tool_rounds"] == 1
    assert cities == ["Lahore"]
    assert len(gateway.calls) == 2
    second_model_messages = gateway.calls[1]
    assert isinstance(second_model_messages[0], SystemMessage)
    assert isinstance(second_model_messages[-1], ToolMessage)
    assert second_model_messages[-1].tool_call_id == "weather-call-1"
    assert len(result["messages"]) == 4
    assert isinstance(result["messages"][-2], ToolMessage)
    assert result["messages"][-1].content == "Lahore is sunny at 35°C."


def test_travel_graph_stops_repeated_tool_calls_at_the_configured_limit() -> None:
    """Repeated model tool requests must not create an unbounded paid loop."""

    cities: list[str] = []
    gateway = FakeModelGateway(
        [
            weather_tool_call(1),
            weather_tool_call(2),
            weather_tool_call(3),
        ]
    )
    graph = build_travel_graph(
        model_gateway=gateway,
        tools=[create_weather_tool(cities)],
        max_tool_rounds=2,
    )

    with pytest.raises(ToolRoundLimitError, match="tool-round limit"):
        asyncio.run(
            graph.ainvoke(
                {
                    "messages": [HumanMessage(content="Weather in Lahore?")],
                    "locale": "en-PK",
                }
            )
        )

    assert cities == ["Lahore", "Lahore"]
    assert len(gateway.calls) == 3
