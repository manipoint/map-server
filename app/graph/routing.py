"""Conditional travel-graph routing."""

from typing import Literal

from langchain_core.messages import AIMessage

from app.graph.exceptions import ToolRoundLimitError
from app.graph.state import TravelGraphState
from app.graph.tools import ITINERARY_SUBMISSION_TOOL_NAME


def route_after_model(
    state: TravelGraphState, *, max_tool_rounds: int
) -> Literal["execute_tools", "capture_itinerary", "build_response"]:
    """Choose the next step after a model response."""

    messages = state["messages"]

    if not messages:
        raise ValueError("Travel graph has no messages")
    response = messages[-1]

    if not isinstance(response, AIMessage):
        raise ValueError("Travel model did not return an AI message")

    if not response.tool_calls:
        return "build_response"

    if any(
        tool_call["name"] == ITINERARY_SUBMISSION_TOOL_NAME
        for tool_call in response.tool_calls
    ):
        return "capture_itinerary"
    completed_rounds = state.get("tool_rounds", 0)
    if completed_rounds >= max_tool_rounds:
        raise ToolRoundLimitError("Travel graph exceeded its tool-round limit")
    return "execute_tools"
