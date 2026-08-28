"""Tests for travel graph construction."""

import asyncio
from collections.abc import Sequence
from datetime import date
from uuid import uuid4

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool

from app.common.exceptions import ProviderUnavailableError
from app.graph.builder import build_travel_graph
from app.graph.exceptions import ToolRoundLimitError
from app.graph.schemas.trips import ActiveTripContext
from app.graph.tools import ITINERARY_SUBMISSION_TOOL_NAME


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


def create_unavailable_weather_tool() -> StructuredTool:
    """Create a weather tool with one safe expected provider failure."""

    async def get_current_weather(city: str) -> dict[str, object]:
        raise ProviderUnavailableError("secret provider failure detail")

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


def itinerary_submission_call() -> AIMessage:
    """Build one valid final itinerary handoff from the model."""

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": ITINERARY_SUBMISSION_TOOL_NAME,
                "args": {
                    "summary": "Two-day London culture plan.",
                    "items": [
                        {
                            "day_number": 1,
                            "item_type": "place",
                            "title": "British Museum",
                        },
                        {
                            "day_number": 2,
                            "item_type": "place",
                            "title": "Hyde Park",
                        },
                    ],
                },
                "id": "itinerary-call-1",
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
    assert isinstance(gateway.calls[0][1], SystemMessage)
    assert "Do not call submit_itinerary" in gateway.calls[0][1].content
    assert gateway.calls[0][2].content == "Plan Lahore trip"
    assert len(result["messages"]) == 2
    assert all(not isinstance(message, SystemMessage) for message in result["messages"])
    assert result["messages"][-1].content == "Here is your Lahore itinerary."


def test_travel_graph_captures_an_itinerary_without_executing_a_tool() -> None:
    """A final itinerary handoff should produce typed state and concise text."""

    gateway = FakeModelGateway([itinerary_submission_call()])
    graph = build_travel_graph(
        model_gateway=gateway,
        tools=[create_weather_tool()],
        max_tool_rounds=2,
    )
    trip_id = uuid4()

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="Plan my London trip")],
                "locale": "en-PK",
                "trip_id": trip_id,
                "trip_context": ActiveTripContext(
                    origin=None,
                    destination="London",
                    start_date=date(2026, 9, 10),
                    end_date=date(2026, 9, 11),
                ),
            }
        )
    )

    assert result["trip_id"] == trip_id
    assert result["generated_itinerary"].summary == ("Two-day London culture plan.")
    assert len(result["generated_itinerary"].items) == 2
    assert result["assistant_response"] == (
        "Two-day London culture plan. I created a 2-day itinerary draft for your trip."
    )
    assert "tool_rounds" not in result
    assert len(gateway.calls) == 1
    assert '"destination":"London"' in gateway.calls[0][1].content


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


def test_travel_graph_recovers_from_an_expected_weather_provider_failure() -> None:
    """The model should turn a safe tool error into a useful final response."""

    gateway = FakeModelGateway(
        [
            weather_tool_call(1),
            AIMessage(content="Verified weather is temporarily unavailable."),
        ]
    )
    graph = build_travel_graph(
        model_gateway=gateway,
        tools=[create_unavailable_weather_tool()],
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

    assert result["assistant_response"] == (
        "Verified weather is temporarily unavailable."
    )
    tool_message = gateway.calls[1][-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.status == "error"
    assert tool_message.content == "Verified travel data is temporarily unavailable."
    assert "secret provider failure detail" not in tool_message.content
