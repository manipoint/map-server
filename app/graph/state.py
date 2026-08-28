"""Travel graph state definitions."""

from typing import Annotated, NotRequired, TypedDict
from uuid import UUID

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.graph.schemas.itineraries import GeneratedItinerary
from app.graph.schemas.trips import ActiveTripContext


class TravelGraphState(TypedDict):
    """State shared by travel-assistant graph nodes."""

    messages: Annotated[list[BaseMessage], add_messages]
    locale: str
    trip_id: UUID | None
    generated_itinerary: NotRequired[GeneratedItinerary]
    assistant_response: NotRequired[str]
    trip_context: ActiveTripContext | None
    error_code: NotRequired[str]
    tool_rounds: NotRequired[int]
