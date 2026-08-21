"""Model-invocation graph nodes."""

from langchain_core.messages import AIMessage, SystemMessage

from app.graph.prompts import TRAVEL_ASSISTANT_SYSTEM_PROMPT
from app.graph.state import TravelGraphState
from app.graph.subgraphs.model_gateway import ModelGateway


async def invoke_travel_model(
    state: TravelGraphState,
    *,
    model_gateway: ModelGateway,
) -> dict[str, list[AIMessage]]:
    """Generate one assistant response from the current travel conversation."""
    model_messages = [
        SystemMessage(content=TRAVEL_ASSISTANT_SYSTEM_PROMPT),
        *state["messages"],
    ]
    response = await model_gateway.generate(messages=model_messages)
    return {"messages": [response]}
