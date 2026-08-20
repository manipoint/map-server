"""Travel graph construction."""

from langgraph.graph import END, START, StateGraph

from app.graph.nodes.models import invoke_travel_model
from app.graph.nodes.responses import build_assistant_response
from app.graph.state import TravelGraphState
from app.graph.subgraphs.model_gateway import ModelGateway


def build_travel_graph(*, model_gateway: ModelGateway):
    """Build the minimal travel response graph with injected model access."""

    graph = StateGraph(TravelGraphState)

    async def invoke_model_node(state: TravelGraphState) -> dict[str, list]:
        return await invoke_travel_model(state=state, model_gateway=model_gateway)

    graph.add_node("invoke_model", invoke_model_node)
    graph.add_node("build_response", build_assistant_response)

    graph.add_edge(START, "invoke_model")
    graph.add_edge("invoke_model", "build_response")
    graph.add_edge("build_response", END)

    return graph.compile()
