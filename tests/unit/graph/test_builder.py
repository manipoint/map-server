"""Tests for travel graph construction."""

import asyncio
from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.graph.builder import build_travel_graph


class FakeModelGateway:
    """Deterministic gateway double for graph tests."""

    def __init__(self) -> None:
        self.calls: list[list[BaseMessage]] = []

    async def generate(self, *, messages: Sequence[BaseMessage]) -> AIMessage:
        """Record input and return a fixed assistant reply."""

        self.calls.append(list(messages))
        return AIMessage(content="Here is your Lahore itinerary.")


def test_travel_graph_generates_a_final_assistant_response() -> None:
    """Graph should invoke the gateway and build the final text response."""

    gateway = FakeModelGateway()
    graph = build_travel_graph(model_gateway=gateway)

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
