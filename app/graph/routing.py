"""Conditional travel-graph routing."""

from typing import Literal

from langchain_core.messages import AIMessage

from app.graph.exceptions import ToolRoundLimitError
from app.graph.state import TravelGraphState


def route_after_model(
    state: TravelGraphState, *, max_tool_rounds: int
) -> Literal["execute_tools", "build_response"]:
    """Route model tool requests to execution, otherwise finalize text."""

    messages = state["messages"]

    if not messages:
        raise ValueError("Travel graph has no messages")
    response = messages[-1]

    if not isinstance(response, AIMessage):
        raise ValueError("Travel model did not return an AI message")

    if response.tool_calls:
        completed_rounds = state.get("tool_rounds", 0)
        if completed_rounds >= max_tool_rounds:
            raise ToolRoundLimitError("Travel graph exceeded its tool-round limit")
        return "execute_tools"
    return "build_response"
