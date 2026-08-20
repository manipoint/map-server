"""Travel graph state definitions."""

from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class TravelGraphState(TypedDict):
    """State shared by travel-assistant graph nodes."""

    messages: Annotated[list[BaseMessage], add_messages]
    locale: str
    assistant_response: NotRequired[str]
    error_code: NotRequired[str]
