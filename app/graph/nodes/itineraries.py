"""Capture validated itinerary submissions from the model."""

from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.graph.exceptions import InvalidItinerarySubmissionError
from app.graph.schemas.itineraries import GeneratedItinerary
from app.graph.state import TravelGraphState
from app.graph.tools import ITINERARY_SUBMISSION_TOOL_NAME


def capture_itinerary_submission(
    state: TravelGraphState,
) -> dict[str, GeneratedItinerary]:
    """Validate and capture one itinerary submission without side effects."""

    trip_context = state.get("trip_context")
    if state["trip_id"] is None or trip_context is None:
        raise InvalidItinerarySubmissionError(
            "Itinerary submission requires trip context"
        )

    message = state["messages"]
    if not message:
        raise InvalidItinerarySubmissionError(
            "Itinerary submission has no model message"
        )

    response = message[-1]
    if not isinstance(response, AIMessage):
        raise InvalidItinerarySubmissionError(
            "Itinerary submission must come from the model"
        )

    tool_calls = response.tool_calls
    if len(tool_calls) != 1:
        raise InvalidItinerarySubmissionError(
            "Itinerary submission must be the only tool call"
        )
    tool_call = tool_calls[0]
    if tool_call["name"] != ITINERARY_SUBMISSION_TOOL_NAME:
        raise InvalidItinerarySubmissionError("Model did not submit an itinerary")

    try:
        generated_itinerary = GeneratedItinerary.model_validate(tool_call["args"])
        submitted_days = {item.day_number for item in generated_itinerary.items}
        expected_days = set(range(1, trip_context.day_count + 1))
        if submitted_days != expected_days:
            raise InvalidItinerarySubmissionError(
                "Submitted itinerary must cover every trip day"
            )
    except ValidationError as error:
        raise InvalidItinerarySubmissionError(
            "Model submitted invalid itinerary arguments"
        ) from error

    return {
        "generated_itinerary": generated_itinerary,
    }


def build_itinerary_response(
    state: TravelGraphState,
) -> dict[str, str]:
    """Build a concise response for a captured itinerary."""

    generated = state.get("generated_itinerary")
    if generated is None:
        raise InvalidItinerarySubmissionError("Captured itinerary is missing")

    day_count = len({item.day_number for item in generated.items})
    return {
        "assistant_response": (
            f"{generated.summary} "
            f"I created a {day_count}-day itinerary draft for your trip."
        )
    }
