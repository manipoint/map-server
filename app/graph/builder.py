"""Travel graph construction."""

from collections.abc import Sequence

from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.itineraries import (
    build_itinerary_response,
    capture_itinerary_submission,
)
from app.graph.nodes.models import invoke_travel_model
from app.graph.nodes.responses import build_assistant_response
from app.graph.nodes.tools import create_tool_node, increment_tool_round
from app.graph.routing import route_after_model
from app.graph.state import TravelGraphState
from app.graph.subgraphs.model_gateway import ModelGateway
from app.services.deal_discovery_service import DealDiscoveryService
from app.services.planning_research_service import PlanningResearchService
from app.services.standalone_search_service import StandaloneSearchService


def build_travel_graph(
    *,
    model_gateway: ModelGateway,
    tools: Sequence[BaseTool],
    max_tool_rounds: int,
    research_service: PlanningResearchService | None = None,
    standalone_service: StandaloneSearchService | None = None,
    deal_discovery_service: DealDiscoveryService | None = None,
):
    """Build the minimal travel response graph with injected model access."""

    if research_service is not None:
        from app.graph.planning_builder import build_planning_graph

        return build_planning_graph(
            model_gateway=model_gateway,
            research_service=research_service,
            standalone_service=standalone_service,
            deal_discovery_service=deal_discovery_service,
        )

    graph = StateGraph(TravelGraphState)

    async def invoke_model_node(state: TravelGraphState) -> dict[str, list]:
        return await invoke_travel_model(state=state, model_gateway=model_gateway)

    def route_model_node(state: TravelGraphState) -> str:
        return route_after_model(state=state, max_tool_rounds=max_tool_rounds)

    tool_node = create_tool_node(tools=tools)
    graph.add_node(
        "invoke_model",
        invoke_model_node,
    )
    graph.add_node(
        "increment_tool_round",
        increment_tool_round,
    )
    graph.add_node(
        "execute_tools",
        tool_node,
    )
    graph.add_node(
        "build_response",
        build_assistant_response,
    )
    graph.add_node(
        "capture_itinerary",
        capture_itinerary_submission,
    )
    graph.add_node(
        "build_itinerary_response",
        build_itinerary_response,
    )

    graph.add_edge(START, "invoke_model")
    graph.add_conditional_edges(
        "invoke_model",
        route_model_node,
        {
            "execute_tools": "increment_tool_round",
            "capture_itinerary": "capture_itinerary",
            "build_response": "build_response",
        },
    )
    graph.add_edge("increment_tool_round", "execute_tools")
    graph.add_edge("execute_tools", "invoke_model")
    graph.add_edge("build_response", END)
    graph.add_edge("capture_itinerary", "build_itinerary_response")
    graph.add_edge("build_itinerary_response", END)

    return graph.compile()
