"""Model-invocation graph nodes."""

from langchain_core.messages import AIMessage

from app.graph.state import TravelGraphState
from app.graph.subgraphs.model_gateway import ModelGateway


async def invoke_travel_model(
    state: TravelGraphState,
    *,
    model_gateway: ModelGateway,
) -> dict[str, list[AIMessage]]:
    """Generate one assistant response from the current travel conversation."""
    response = await model_gateway.generate(messages=state["messages"])
    return {"messages": [response]}
