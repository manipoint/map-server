"""Tests for public itinerary request and response schemas."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas.itineraries import ItineraryCreateRequest, ItineraryResponse


def test_create_request_parses_a_bounded_timeline() -> None:
    """Valid item data should become strict domain drafts."""

    request = ItineraryCreateRequest.model_validate(
        {
            "items": [
                {
                    "day_number": 1,
                    "position": 1,
                    "item_type": "place",
                    "title": " British Museum ",
                }
            ]
        }
    )

    assert request.items[0].title == "British Museum"
    assert request.items[0].item_type == "place"


@pytest.mark.parametrize("items", [[], [{}]])
def test_create_request_rejects_missing_or_invalid_items(
    items: list[object],
) -> None:
    """The public contract should reject empty and malformed timelines."""

    with pytest.raises(ValidationError):
        ItineraryCreateRequest.model_validate({"items": items})


def test_response_serializes_enums_and_aware_times() -> None:
    """Flutter should receive stable JSON strings for status, type, and times."""

    timestamp = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    response = ItineraryResponse.model_validate(
        {
            "id": uuid4(),
            "trip_id": uuid4(),
            "version": 2,
            "status": "saved",
            "created_at": timestamp,
            "updated_at": timestamp,
            "items": [
                {
                    "id": uuid4(),
                    "day_number": 1,
                    "position": 1,
                    "item_type": "activity",
                    "title": "Walking tour",
                    "description": None,
                    "location_name": "London",
                    "starts_at": timestamp,
                    "ends_at": None,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            ],
        }
    )

    dumped = response.model_dump(mode="json")
    assert dumped["status"] == "saved"
    assert dumped["items"][0]["item_type"] == "activity"
    assert dumped["items"][0]["starts_at"] == "2026-08-27T12:00:00Z"
