"""Tests for the LangGraph airport-resolution tool adapter."""

import asyncio

from app.graph.tools import create_airport_resolution_tool
from app.providers.airports.schemas import AirportResolution, AirportSearchInput


class FakeAirportMcpClient:
    """Record and answer one airport-resolution request."""

    def __init__(self) -> None:
        self.requests: list[AirportSearchInput] = []

    async def resolve_airport(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportResolution:
        """Return a deterministic verified code."""

        self.requests.append(request)
        return AirportResolution(
            status="resolved",
            query=request.query,
            iata_code="LHR",
        )


def test_airport_graph_tool_exposes_resolution_contract() -> None:
    """The model should see bounded query and result controls."""

    tool = create_airport_resolution_tool(mcp_client=FakeAirportMcpClient())

    assert tool.name == "resolve_airport"
    assert "never guess" in tool.description
    assert tool.args_schema is AirportSearchInput
    schema = tool.args_schema.model_json_schema()
    assert schema["required"] == ["query"]
    assert schema["properties"]["max_results"]["maximum"] == 5


def test_airport_graph_tool_normalizes_delegates_and_serializes() -> None:
    """One invocation should cross MCP once and return JSON-safe data."""

    async def exercise() -> None:
        client = FakeAirportMcpClient()
        tool = create_airport_resolution_tool(mcp_client=client)

        result = await tool.ainvoke({"query": " London ", "max_results": 3})

        assert client.requests == [AirportSearchInput(query="London", max_results=3)]
        assert result == {
            "status": "resolved",
            "query": "London",
            "iata_code": "LHR",
            "options": [],
        }

    asyncio.run(exercise())
