"""Build final travel-assistant responses from graph state."""

from langchain_core.messages import AIMessage

from app.graph.state import TravelGraphState


def build_assistant_response(state: TravelGraphState) -> dict[str, str]:
    """Extract a non-empty text response from the final AI graph message."""

    messages = state["messages"]

    if not messages:
        raise ValueError("Travel graph has no messages")

    final_message = messages[-1]

    if not isinstance(final_message, AIMessage):
        raise ValueError("Travel graph did not finish with an AI message")

    if not isinstance(final_message.content, str):
        raise ValueError("Travel graph response must be plain text")

    response = final_message.content.strip()

    if not response:
        raise ValueError("Travel graph response must not be blank")

    return {"assistant_response": response}
