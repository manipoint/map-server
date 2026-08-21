"""Tests for conditional travel-graph routing."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.graph.exceptions import ToolRoundLimitError
from app.graph.routing import route_after_model
from app.graph.state import TravelGraphState


def test_route_after_model_sends_text_to_response_builder() -> None:
    """A final text response should leave the model/tool loop."""

    state: TravelGraphState = {
        "messages": [AIMessage(content="Sunny in Lahore")],
        "locale": "en-PK",
    }

    assert route_after_model(state, max_tool_rounds=2) == "build_response"


def test_route_after_model_sends_tool_calls_to_execution() -> None:
    """A requested weather tool should be executed before another model call."""

    state: TravelGraphState = {
        "messages": [
            AIMessage(
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
        ],
        "locale": "en-PK",
    }

    assert route_after_model(state, max_tool_rounds=2) == "execute_tools"


def test_route_after_model_rejects_empty_messages() -> None:
    """Routing without a model result should fail clearly."""

    state: TravelGraphState = {"messages": [], "locale": "en-PK"}

    with pytest.raises(ValueError, match="no messages"):
        route_after_model(
            state,
            max_tool_rounds=2,
        )


def test_route_after_model_rejects_tool_call_at_round_limit() -> None:
    """A model must not start another tool round after reaching the cost limit."""

    state: TravelGraphState = {
        "messages": [
            AIMessage(
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
        ],
        "locale": "en-PK",
        "tool_rounds": 2,
    }

    with pytest.raises(ToolRoundLimitError, match="tool-round limit"):
        route_after_model(state, max_tool_rounds=2)


def test_route_after_model_allows_text_at_round_limit() -> None:
    """A final text response remains valid after all allowed tool rounds."""

    state: TravelGraphState = {
        "messages": [AIMessage(content="Lahore is sunny today.")],
        "locale": "en-PK",
        "tool_rounds": 2,
    }

    assert route_after_model(state, max_tool_rounds=2) == "build_response"


def test_route_after_model_rejects_a_non_ai_final_message() -> None:
    """Only an AI response can choose the next graph branch."""

    state: TravelGraphState = {
        "messages": [HumanMessage(content="Weather in Lahore")],
        "locale": "en-PK",
    }

    with pytest.raises(ValueError, match="did not return an AI message"):
        route_after_model(
            state,
            max_tool_rounds=2,
        )
