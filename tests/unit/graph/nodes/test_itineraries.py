"""Tests for capturing structured itinerary model submissions."""

from datetime import date
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.graph.exceptions import InvalidItinerarySubmissionError
from app.graph.nodes.itineraries import (
    build_itinerary_response,
    capture_itinerary_submission,
)
from app.graph.schemas.itineraries import GeneratedItinerary
from app.graph.schemas.trips import ActiveTripContext
from app.graph.state import TravelGraphState
from app.graph.tools import ITINERARY_SUBMISSION_TOOL_NAME


def valid_itinerary_arguments() -> dict[str, object]:
    """Build one valid structured itinerary handoff."""

    return {
        "summary": "Two-day London museum plan",
        "items": [
            {
                "day_number": 1,
                "item_type": "place",
                "title": "British Museum",
                "location_name": "London",
                "starts_at": "2026-09-10T09:00:00Z",
                "ends_at": "2026-09-10T11:00:00Z",
            },
            {
                "day_number": 2,
                "item_type": "meal",
                "title": "Local lunch",
                "location_name": "London",
            },
        ],
    }


def active_trip_context() -> ActiveTripContext:
    """Build one two-day active trip context."""

    return ActiveTripContext(
        origin=None,
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 11),
    )


def submission_message(
    *,
    arguments: dict[str, object] | None = None,
    name: str = ITINERARY_SUBMISSION_TOOL_NAME,
) -> AIMessage:
    """Build a model message containing one itinerary tool call."""

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": arguments if arguments is not None else {},
                "id": "itinerary-call-1",
                "type": "tool_call",
            }
        ],
    )


def test_capture_itinerary_submission_returns_validated_output() -> None:
    """A valid handoff should become typed graph state without side effects."""

    trip_id = uuid4()
    state: TravelGraphState = {
        "messages": [submission_message(arguments=valid_itinerary_arguments())],
        "locale": "en-PK",
        "trip_id": trip_id,
        "trip_context": active_trip_context(),
    }

    update = capture_itinerary_submission(state)

    assert isinstance(update["generated_itinerary"], GeneratedItinerary)
    assert update["generated_itinerary"].summary == "Two-day London museum plan"
    assert update["generated_itinerary"].items[0].title == "British Museum"


def test_capture_itinerary_submission_requires_trip_context() -> None:
    """A standalone chat must not create an itinerary accidentally."""

    state: TravelGraphState = {
        "messages": [submission_message(arguments=valid_itinerary_arguments())],
        "locale": "en-PK",
        "trip_id": None,
    }

    with pytest.raises(InvalidItinerarySubmissionError, match="trip context"):
        capture_itinerary_submission(state)


def test_capture_itinerary_submission_requires_a_model_message() -> None:
    """An empty graph history should fail with a graph-specific error."""

    state: TravelGraphState = {
        "messages": [],
        "locale": "en-PK",
        "trip_id": uuid4(),
        "trip_context": active_trip_context(),
    }

    with pytest.raises(InvalidItinerarySubmissionError, match="no model message"):
        capture_itinerary_submission(state)


def test_capture_itinerary_submission_rejects_a_human_message() -> None:
    """Only a model response can provide the structured handoff."""

    state: TravelGraphState = {
        "messages": [HumanMessage(content="Build my itinerary")],
        "locale": "en-PK",
        "trip_id": uuid4(),
        "trip_context": active_trip_context(),
    }

    with pytest.raises(InvalidItinerarySubmissionError, match="come from the model"):
        capture_itinerary_submission(state)


def test_capture_itinerary_submission_rejects_multiple_tool_calls() -> None:
    """Mixed tool calls must fail before any external tool can execute."""

    message = submission_message(arguments=valid_itinerary_arguments())
    message.tool_calls.append(
        {
            "name": "get_current_weather",
            "args": {"city": "London"},
            "id": "weather-call-1",
            "type": "tool_call",
        }
    )
    state: TravelGraphState = {
        "messages": [message],
        "locale": "en-PK",
        "trip_id": uuid4(),
        "trip_context": active_trip_context(),
    }

    with pytest.raises(InvalidItinerarySubmissionError, match="only tool call"):
        capture_itinerary_submission(state)


def test_capture_itinerary_submission_rejects_the_wrong_tool() -> None:
    """The capture node should accept only its dedicated handoff tool."""

    state: TravelGraphState = {
        "messages": [
            submission_message(
                arguments=valid_itinerary_arguments(),
                name="get_current_weather",
            )
        ],
        "locale": "en-PK",
        "trip_id": uuid4(),
        "trip_context": active_trip_context(),
    }

    with pytest.raises(InvalidItinerarySubmissionError, match="did not submit"):
        capture_itinerary_submission(state)


def test_capture_itinerary_submission_wraps_schema_validation_errors() -> None:
    """Invalid model arguments should not leak raw schema errors."""

    state: TravelGraphState = {
        "messages": [submission_message(arguments={"summary": "Empty", "items": []})],
        "locale": "en-PK",
        "trip_id": uuid4(),
        "trip_context": active_trip_context(),
    }

    with pytest.raises(
        InvalidItinerarySubmissionError,
        match="invalid itinerary arguments",
    ) as captured:
        capture_itinerary_submission(state)

    assert isinstance(captured.value.__cause__, ValidationError)


def test_capture_itinerary_submission_rejects_incomplete_trip_days() -> None:
    """A complete generated itinerary must include every active trip day."""

    arguments = valid_itinerary_arguments()
    items = arguments["items"]
    assert isinstance(items, list)
    arguments["items"] = items[:1]
    state: TravelGraphState = {
        "messages": [submission_message(arguments=arguments)],
        "locale": "en-PK",
        "trip_id": uuid4(),
        "trip_context": active_trip_context(),
    }

    with pytest.raises(
        InvalidItinerarySubmissionError,
        match="cover every trip day",
    ):
        capture_itinerary_submission(state)


def test_capture_itinerary_submission_rejects_day_outside_trip_duration() -> None:
    """A generated item must not use a day beyond the active trip duration."""

    arguments = valid_itinerary_arguments()
    items = arguments["items"]
    assert isinstance(items, list)
    final_item = items[-1]
    assert isinstance(final_item, dict)
    final_item["day_number"] = 3
    state: TravelGraphState = {
        "messages": [submission_message(arguments=arguments)],
        "locale": "en-PK",
        "trip_id": uuid4(),
        "trip_context": active_trip_context(),
    }

    with pytest.raises(
        InvalidItinerarySubmissionError,
        match="cover every trip day",
    ):
        capture_itinerary_submission(state)


def test_build_itinerary_response_reports_the_unique_day_count() -> None:
    """Repeated items on one day should not inflate the displayed duration."""

    generated = GeneratedItinerary.model_validate(
        {
            "summary": "London culture plan.",
            "items": [
                {
                    "day_number": 1,
                    "item_type": "place",
                    "title": "British Museum",
                },
                {
                    "day_number": 1,
                    "item_type": "meal",
                    "title": "Local lunch",
                },
                {
                    "day_number": 2,
                    "item_type": "place",
                    "title": "Hyde Park",
                },
            ],
        }
    )
    state: TravelGraphState = {
        "messages": [],
        "locale": "en-PK",
        "trip_id": uuid4(),
        "generated_itinerary": generated,
    }

    assert build_itinerary_response(state) == {
        "assistant_response": (
            "London culture plan. I created a 2-day itinerary draft for your trip."
        )
    }


def test_build_itinerary_response_requires_a_captured_itinerary() -> None:
    """The response node should fail clearly when capture did not run."""

    state: TravelGraphState = {
        "messages": [],
        "locale": "en-PK",
        "trip_id": uuid4(),
    }

    with pytest.raises(
        InvalidItinerarySubmissionError,
        match="Captured itinerary is missing",
    ):
        build_itinerary_response(state)
