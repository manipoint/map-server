"""Tests for itinerary domain contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.itineraries import (
    ItineraryItemDraft,
    ItineraryItemType,
    ItineraryStatus,
)


def test_itinerary_status_values_match_persisted_contract() -> None:
    """Itinerary lifecycle values should remain stable in PostgreSQL."""

    assert [status.value for status in ItineraryStatus] == [
        "draft",
        "saved",
        "superseded",
    ]


def test_itinerary_item_type_values_are_provider_independent() -> None:
    """Timeline categories should not depend on a travel provider."""

    assert [item_type.value for item_type in ItineraryItemType] == [
        "flight",
        "hotel",
        "place",
        "activity",
        "meal",
        "transfer",
        "note",
    ]
    assert all(isinstance(item_type, str) for item_type in ItineraryItemType)


def test_itinerary_item_draft_normalizes_valid_display_fields() -> None:
    """Valid input should retain aware times and trim display strings."""

    starts_at = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    ends_at = starts_at + timedelta(hours=2)

    item = ItineraryItemDraft(
        day_number=1,
        position=2,
        item_type="place",
        title="  British Museum  ",
        description="  Explore the galleries  ",
        location_name="  London  ",
        starts_at=starts_at,
        ends_at=ends_at,
    )

    assert item.item_type is ItineraryItemType.PLACE
    assert item.title == "British Museum"
    assert item.description == "Explore the galleries"
    assert item.location_name == "London"
    assert item.starts_at == starts_at
    assert item.ends_at == ends_at


@pytest.mark.parametrize("field_name", ["starts_at", "ends_at"])
def test_itinerary_item_draft_rejects_naive_datetime(field_name: str) -> None:
    """Timeline timestamps must include an explicit timezone offset."""

    values = {
        "day_number": 1,
        "position": 1,
        "item_type": "activity",
        "title": "Walking tour",
        field_name: datetime(2026, 9, 10, 9, 0),
    }

    with pytest.raises(ValidationError):
        ItineraryItemDraft.model_validate(values)


@pytest.mark.parametrize("duration", [timedelta(0), timedelta(minutes=-1)])
def test_itinerary_item_draft_rejects_invalid_time_order(
    duration: timedelta,
) -> None:
    """An item's end must be strictly later than its start."""

    starts_at = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="ends_at must be after starts_at"):
        ItineraryItemDraft(
            day_number=1,
            position=1,
            item_type="activity",
            title="Walking tour",
            starts_at=starts_at,
            ends_at=starts_at + duration,
        )


@pytest.mark.parametrize(
    "values",
    [
        {"day_number": 0},
        {"position": 0},
        {"title": "   "},
        {"description": "   "},
        {"location_name": "   "},
        {"unexpected": "value"},
    ],
)
def test_itinerary_item_draft_rejects_invalid_fields(
    values: dict[str, object],
) -> None:
    """Invalid ordering, empty text, and unknown fields should be rejected."""

    payload: dict[str, object] = {
        "day_number": 1,
        "position": 1,
        "item_type": "note",
        "title": "Remember passport",
    }
    payload.update(values)

    with pytest.raises(ValidationError):
        ItineraryItemDraft.model_validate(payload)
