"""Tests for public trip REST schemas."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas.trips import (
    TripCreateRequest,
    TripListResponse,
    TripResponse,
)
from app.database.models.trip import Trip
from app.domain.trips import TripStatus


def create_trip() -> Trip:
    """Create a complete persistence object for response conversion tests."""

    return Trip(
        id=uuid4(),
        user_id=uuid4(),
        title="London museums",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        status=TripStatus.DRAFT.value,
        created_at=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
    )


def test_trip_create_request_normalizes_text() -> None:
    """Creation input should trim bounded human-readable fields."""

    request = TripCreateRequest(
        title=" London museums ",
        origin=" Lahore ",
        destination=" London ",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
    )

    assert request.title == "London museums"
    assert request.origin == "Lahore"
    assert request.destination == "London"


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        (date(2026, 9, 10), date(2026, 9, 10)),
        (date(2026, 9, 11), date(2026, 9, 10)),
    ],
)
def test_trip_create_request_rejects_invalid_date_range(
    start_date: date,
    end_date: date,
) -> None:
    """A saved trip should contain at least one overnight interval."""

    with pytest.raises(ValidationError, match="end_date must be after start_date"):
        TripCreateRequest(
            destination="London",
            start_date=start_date,
            end_date=end_date,
        )


def test_trip_create_request_rejects_same_route_endpoints() -> None:
    """Equivalent origin and destination values should be rejected."""

    with pytest.raises(
        ValidationError,
        match="origin and destination must be different",
    ):
        TripCreateRequest(
            origin=" London ",
            destination="london",
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": " "},
        {"origin": "x"},
        {"destination": "x"},
        {"unexpected": True},
    ],
)
def test_trip_create_request_rejects_invalid_bounded_or_unknown_input(
    overrides: dict[str, object],
) -> None:
    """Creation input should enforce text bounds and reject extra fields."""

    values: dict[str, object] = {
        "destination": "London",
        "start_date": date(2026, 9, 10),
        "end_date": date(2026, 9, 12),
    }
    values.update(overrides)

    with pytest.raises(ValidationError):
        TripCreateRequest(**values)


def test_trip_response_converts_persistence_attributes() -> None:
    """The public response should exclude ownership and normalize status."""

    trip = create_trip()

    response = TripResponse.model_validate(trip)
    payload = response.model_dump(mode="json")

    assert response.id == trip.id
    assert response.status is TripStatus.DRAFT
    assert "user_id" not in payload
    assert payload["status"] == "draft"
    assert payload["start_date"] == "2026-09-10"
    assert payload["created_at"] == "2026-08-27T09:00:00Z"


def test_trip_list_response_contains_items_and_cursor() -> None:
    """A trip page should expose converted resources and its opaque cursor."""

    trip_response = TripResponse.model_validate(create_trip())

    response = TripListResponse(
        items=[trip_response],
        next_cursor="opaque-cursor",
    )

    assert response.items == [trip_response]
    assert response.next_cursor == "opaque-cursor"
