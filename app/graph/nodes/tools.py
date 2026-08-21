"""Tool-execution graph nodes."""

from collections.abc import Sequence

from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode

from app.graph.state import TravelGraphState


def create_tool_node(*, tools: Sequence[BaseTool]) -> ToolNode:
    """Create the graph node that executes approved model tool calls."""
    if not tools:
        raise ValueError("at least one graph tool is required")

    return ToolNode(list(tools), name="execute_tools")


def increment_tool_round(
    state: TravelGraphState,
) -> dict[str, int]:
    """Record one model-to-tool execution round."""
    return {"tool_rounds": state.get("tool_rounds", 0) + 1}
