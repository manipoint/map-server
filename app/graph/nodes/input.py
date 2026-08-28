"""Build LangGraph input from persisted conversation messages."""

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.database.models.message import Message
from app.database.models.trip import Trip
from app.graph.schemas.trips import ActiveTripContext
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
    *,
    messages: Sequence[Message],
    locale: str,
    trip: Trip | None = None,
) -> TravelGraphState:
    """Build the initial bounded state for one travel graph invocation."""

    trip_context = (
        ActiveTripContext(
            origin=(
                trip.origin_location.canonical_name
                if trip.origin_location is not None
                else trip.origin
            ),
            destination=(
                trip.destination_location.canonical_name
                if trip.destination_location is not None
                else trip.destination
            ),
            start_date=trip.start_date,
            end_date=trip.end_date,
        )
        if trip is not None
        else None
    )
    return {
        "messages": to_langchain_messages(messages),
        "locale": locale,
        "trip_id": trip.id if trip is not None else None,
        "trip_context": trip_context,
    }
