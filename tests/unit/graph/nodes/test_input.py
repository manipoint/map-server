"""Tests for converting persisted data into bounded travel graph state."""

from datetime import date
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from app.database.models.message import Message
from app.database.models.trip import Trip
from app.graph.nodes.input import build_travel_graph_input
from app.graph.schemas.trips import ActiveTripContext


def test_build_travel_graph_input_maps_trusted_active_trip_context() -> None:
    """The model should receive bounded destination and date details."""

    user_id = uuid4()
    trip = Trip(
        id=uuid4(),
        user_id=user_id,
        origin="Karachi",
        destination="Lahore",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        status="draft",
    )
    message = Message(
        id=uuid4(),
        conversation_id=uuid4(),
        client_message_id=uuid4(),
        trip_id=trip.id,
        role="user",
        content="Create my itinerary",
    )

    state = build_travel_graph_input(
        messages=[message],
        locale="en-PK",
        trip=trip,
    )

    assert state["trip_id"] == trip.id
    assert state["trip_context"] == ActiveTripContext(
        origin="Karachi",
        destination="Lahore",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
    )
    assert state["trip_context"].day_count == 3
    assert isinstance(state["messages"][0], HumanMessage)


def test_build_travel_graph_input_marks_standalone_chat_explicitly() -> None:
    """A chat without a trip should not expose itinerary submission context."""

    state = build_travel_graph_input(
        messages=[],
        locale="en-PK",
    )

    assert state["trip_id"] is None
    assert state["trip_context"] is None


def test_build_travel_graph_input_prefers_canonical_location_names() -> None:
    """Resolved names should prevent repeated ambiguity inside provider tools."""

    trip = Trip(
        id=uuid4(),
        user_id=uuid4(),
        origin="Lahore",
        destination="London",
        origin_location_provider="google",
        origin_provider_location_id="lahore-id",
        origin_canonical_name="Lahore, Pakistan",
        origin_country_code="PK",
        origin_latitude=31.5204,
        origin_longitude=74.3587,
        destination_location_provider="google",
        destination_provider_location_id="london-id",
        destination_canonical_name="London, United Kingdom",
        destination_country_code="GB",
        destination_latitude=51.5074,
        destination_longitude=-0.1278,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        status="draft",
    )

    state = build_travel_graph_input(messages=[], locale="en-PK", trip=trip)

    assert state["trip_context"] is not None
    assert state["trip_context"].origin == "Lahore, Pakistan"
    assert state["trip_context"].destination == "London, United Kingdom"


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        (date(2026, 9, 10), date(2026, 9, 10)),
        (date(2026, 9, 11), date(2026, 9, 10)),
    ],
)
def test_active_trip_context_rejects_invalid_date_order(
    start_date: date,
    end_date: date,
) -> None:
    """Equal or reversed dates should never reach the model."""

    with pytest.raises(ValidationError, match="end_date must be after start_date"):
        ActiveTripContext(
            destination="Lahore",
            start_date=start_date,
            end_date=end_date,
        )
