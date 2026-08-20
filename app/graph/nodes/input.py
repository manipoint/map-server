"""Build LangGraph input from persisted conversation messages."""

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.database.models.message import Message
from app.graph.state import TravelGraphState


def to_langchain_messages(messages: Sequence[Message]) -> list[BaseMessage]:
    """Convert persisted chronological messages to LangChain chat messages."""

    converted_messages: list[BaseMessage] = []
    for message in messages:
        if message.role == "user":
            converted_messages.append(HumanMessage(content=message.content))
        elif message.role == "assistant":
            converted_messages.append(AIMessage(content=message.content))

        else:
            raise ValueError(f"Unsupported persisted message role: {message.role}")

    return converted_messages


def build_travel_graph_input(
    *, messages: Sequence[Message], locale: str
) -> TravelGraphState:
    """Build the initial bounded state for one travel graph invocation."""

    return {"messages": to_langchain_messages(messages), "locale": locale}
