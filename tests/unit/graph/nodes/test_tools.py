"""Tests for travel-graph tool execution nodes."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph

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
        node = create_tool_node(tools=[create_city_tool()])
        builder = StateGraph(TravelGraphState)
        builder.add_node("execute_tools", node)
        builder.add_edge(START, "execute_tools")
        builder.add_edge("execute_tools", END)
        graph = builder.compile()
        model_message = AIMessage(
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

        result = await graph.ainvoke(
            {
                "messages": [model_message],
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
