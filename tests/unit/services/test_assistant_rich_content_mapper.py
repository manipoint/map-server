from datetime import date
from uuid import uuid4

from app.database.models.trip import Trip
from app.graph.schemas.itineraries import GeneratedItinerary
from app.services.assistant_rich_content_mapper import (
    build_itinerary_rich_content,
)


def create_trip(*, title: str | None = None) -> Trip:
    """Return a persisted trip suitable for rich-content mapping."""

    return Trip(
        id=uuid4(),
        user_id=uuid4(),
        title=title,
        origin="Lahore",
        destination="Japan",
        start_date=date(2026, 11, 7),
        end_date=date(2026, 11, 11),
        status="draft",
    )


def create_generated_itinerary() -> GeneratedItinerary:
    """Return deliberately unordered items spanning two itinerary days."""

    return GeneratedItinerary.model_validate(
        {
            "summary": "A five-day Japan itinerary.",
            "items": [
                {
                    "day_number": 2,
                    "item_type": "place",
                    "title": "Visit Fushimi Inari",
                },
                {
                    "day_number": 1,
                    "item_type": "transfer",
                    "title": "Arrival at Narita Airport",
                },
                {
                    "day_number": 1,
                    "item_type": "place",
                    "title": "Visit Senso-ji Temple",
                },
            ],
        }
    )


def test_builds_a_bounded_chronological_itinerary_preview() -> None:
    itinerary_id = uuid4()

    content = build_itinerary_rich_content(
        itinerary_id=itinerary_id,
        trip=create_trip(title="Japan Adventure"),
        generated=create_generated_itinerary(),
    )

    assert content.type == "rich_response"
    assert content.schema_version == 1
    assert len(content.sections) == 1

    preview = content.sections[0]
    assert preview.type == "itinerary_preview"
    assert preview.itinerary_id == itinerary_id
    assert preview.summary.title == "Japan Adventure"
    assert preview.summary.duration_days == 5
    assert preview.summary.traveler_count is None
    assert preview.summary.cities == []
    assert preview.summary.pace is None
    assert [day.day_number for day in preview.days] == [1, 2]
    assert preview.days[0].date == date(2026, 11, 7)
    assert preview.days[0].subtitle == "Arrival at Narita Airport"
    assert preview.days[1].date == date(2026, 11, 8)


def test_uses_destination_when_the_trip_has_no_title() -> None:
    content = build_itinerary_rich_content(
        itinerary_id=uuid4(),
        trip=create_trip(),
        generated=create_generated_itinerary(),
    )

    preview = content.sections[0]
    assert preview.summary.title == "Japan Adventure"


def test_limits_chat_preview_to_seven_days() -> None:
    generated = GeneratedItinerary.model_validate(
        {
            "summary": "A longer Japan itinerary.",
            "items": [
                {
                    "day_number": day_number,
                    "item_type": "activity",
                    "title": f"Activity {day_number}",
                }
                for day_number in range(1, 9)
            ],
        }
    )

    content = build_itinerary_rich_content(
        itinerary_id=uuid4(),
        trip=create_trip(),
        generated=generated,
    )

    preview = content.sections[0]
    assert [day.day_number for day in preview.days] == list(range(1, 8))
