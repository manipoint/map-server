"""Map verified backend results into assistant presentation content."""

from collections import defaultdict
from datetime import timedelta
from uuid import UUID

from app.database.models.trip import Trip
from app.domain.assistant_content import (
    AssistantItineraryDayPreview,
    AssistantItineraryPreview,
    AssistantItinerarySummary,
    AssistantRichContent,
)
from app.graph.schemas.itineraries import GeneratedItinerary, GeneratedItineraryItem


def _group_items_by_day(
    generated: GeneratedItinerary,
) -> dict[int, list[GeneratedItineraryItem]]:
    """Group generated items without relying on their input ordering."""
    items_by_day: dict[int, list[GeneratedItineraryItem]] = defaultdict(list)
    for item in generated.items:
        items_by_day[item.day_number].append(item)
    return dict(items_by_day)


def build_itinerary_rich_content(
    *, itinerary_id: UUID, trip: Trip, generated: GeneratedItinerary
) -> AssistantRichContent:
    """Build a compact chat preview for a persisted itinerary."""
    items_by_day = _group_items_by_day(generated)
    day_previews = [
        AssistantItineraryDayPreview(
            day_number=day_number,
            date=trip.start_date + timedelta(days=day_number - 1),
            title=f"Day {day_number}",
            subtitle=items[0].title,
        )
        for day_number, items in sorted(items_by_day.items())
    ][:7]

    duration_days = (trip.end_date - trip.start_date).days + 1

    summary = AssistantItinerarySummary(
        title=_itinerary_title(trip),
        start_date=trip.start_date,
        end_date=trip.end_date,
        duration_days=duration_days,
        traveler_count=None,
        cities=[],
        pace=None,
        cover_image=None,
    )
    preview = AssistantItineraryPreview(
        id="generated-itinerary",
        title="Your AI-Generated Itinerary",
        itinerary_id=itinerary_id,
        summary=summary,
        days=day_previews,
    )
    return AssistantRichContent(sections=[preview])


def _itinerary_title(trip: Trip) -> str:
    """Return a deterministic itinerary title."""
    if trip.title is not None:
        normalized_title = trip.title.strip()
        if normalized_title:
            return normalized_title

    return f"{trip.destination} Adventure"
