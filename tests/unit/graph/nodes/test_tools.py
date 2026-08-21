"""Tests for travel-graph tool execution nodes."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph

from app.common.exceptions import ProviderUnavailableError
from app.graph.nodes.tools import create_tool_node, increment_tool_round
from app.graph.state import TravelGraphState


def create_city_tool() -> StructuredTool:
    """Create a deterministic async tool for node execution tests."""

    async def get_city(city: str) -> dict[str, str]:
        return {"city": city, "condition": "Sunny"}

    return StructuredTool.from_function(
        coroutine=get_city,
        name="get_current_weather",
        description="Return current weather for a city.",
    )


def create_failing_tool(error: Exception) -> StructuredTool:
    """Create an async weather tool that raises a configured error."""

    async def get_current_weather(city: str) -> dict[str, str]:
        raise error

    return StructuredTool.from_function(
        coroutine=get_current_weather,
        name="get_current_weather",
        description="Return current weather for a city.",
    )


def compile_tool_graph(tool: StructuredTool):
    """Compile one tool node with the runtime context LangGraph supplies."""

    builder = StateGraph(TravelGraphState)
    builder.add_node("execute_tools", create_tool_node(tools=[tool]))
    builder.add_edge(START, "execute_tools")
    builder.add_edge("execute_tools", END)
    return builder.compile()


def weather_tool_call() -> AIMessage:
    """Build one deterministic current-weather tool request."""

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_current_weather",
                "args": {"city": "Lahore"},
                "id": "weather-call-1",
                "type": "tool_call",
            }
        ],
    )


def test_create_tool_node_requires_at_least_one_approved_tool() -> None:
    """An empty tool registry should fail during graph construction."""

    with pytest.raises(ValueError, match="at least one graph tool"):
        create_tool_node(tools=[])


@pytest.mark.parametrize(
    ("existing_rounds", "expected_rounds"),
    [(None, 1), (1, 2)],
)
def test_increment_tool_round(
    existing_rounds: int | None,
    expected_rounds: int,
) -> None:
    """The counter should initialize at one and increment existing state."""

    state: TravelGraphState = {
        "messages": [],
        "locale": "en-PK",
    }
    if existing_rounds is not None:
        state["tool_rounds"] = existing_rounds

    assert increment_tool_round(state) == {"tool_rounds": expected_rounds}


def test_tool_node_executes_an_approved_async_tool_call() -> None:
    """The node should return a ToolMessage linked to the model call ID."""

    async def exercise() -> None:
        graph = compile_tool_graph(create_city_tool())

        result = await graph.ainvoke(
            {
                "messages": [weather_tool_call()],
                "locale": "en-PK",
            }
        )

        assert len(result["messages"]) == 2
        tool_message = result["messages"][-1]
        assert isinstance(tool_message, ToolMessage)
        assert tool_message.name == "get_current_weather"
        assert tool_message.tool_call_id == "weather-call-1"
        assert '"city": "Lahore"' in tool_message.content
        assert '"condition": "Sunny"' in tool_message.content

    asyncio.run(exercise())


def test_tool_node_converts_provider_failure_to_safe_tool_message() -> None:
    """Expected provider errors should reach the model without raw details."""

    async def exercise() -> None:
        graph = compile_tool_graph(
            create_failing_tool(
                ProviderUnavailableError("secret upstream response body")
            )
        )

        result = await graph.ainvoke(
            {
                "messages": [weather_tool_call()],
                "locale": "en-PK",
            }
        )

        tool_message = result["messages"][-1]
        assert isinstance(tool_message, ToolMessage)
        assert tool_message.status == "error"
        assert tool_message.content == (
            "Verified travel data is temporarily unavailable."
        )
        assert "secret upstream response body" not in tool_message.content

    asyncio.run(exercise())


def test_tool_node_allows_unexpected_programming_errors_to_propagate() -> None:
    """Unexpected defects should remain visible to monitoring and retry handling."""

    async def exercise() -> None:
        graph = compile_tool_graph(
            create_failing_tool(RuntimeError("unexpected programming defect"))
        )

        with pytest.raises(RuntimeError, match="unexpected programming defect"):
            await graph.ainvoke(
                {
                    "messages": [weather_tool_call()],
                    "locale": "en-PK",
                }
            )

    asyncio.run(exercise())
