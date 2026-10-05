"""Map verified backend results into assistant presentation content."""

from collections import defaultdict
from datetime import timedelta
from uuid import UUID

from app.common.time import utc_now
from app.database.models.trip import Trip
from app.domain.assistant_content import (
    AssistantHotelCarousel,
    AssistantItineraryDayPreview,
    AssistantItineraryPreview,
    AssistantItinerarySummary,
    AssistantPlaceCard,
    AssistantPlaceCarousel,
    AssistantRichContent,
)
from app.domain.itineraries import MAX_ITINERARY_DAYS, ItineraryItemDraft
from app.domain.trip_requirements import TripRequirements
from app.domain.trip_rules import inclusive_day_count
from app.graph.schemas.itineraries import GeneratedItinerary, to_itinerary_item_drafts
from app.services.planning_research_service import PlanningResearch


def _group_items_by_day(
    generated: GeneratedItinerary,
) -> dict[int, list[ItineraryItemDraft]]:
    """Group generated items without relying on their input ordering."""
    items_by_day: dict[int, list[ItineraryItemDraft]] = defaultdict(list)
    for item in to_itinerary_item_drafts(generated):
        items_by_day[item.day_number].append(item)
    return dict(items_by_day)


def build_itinerary_rich_content(
    *,
    itinerary_id: UUID,
    trip: Trip,
    generated: GeneratedItinerary,
    research: PlanningResearch | None = None,
    requirements: TripRequirements | None = None,
) -> AssistantRichContent:
    """Build a compact chat preview for a persisted itinerary."""
    items_by_day = _group_items_by_day(generated)
    day_previews = [
        AssistantItineraryDayPreview(
            day_number=day_number,
            date=trip.start_date + timedelta(days=day_number - 1),
            title=f"Day {day_number}",
            subtitle=items[0].title,
            activities=items,
        )
        for day_number, items in sorted(items_by_day.items())
    ][:MAX_ITINERARY_DAYS]

    duration_days = inclusive_day_count(trip.start_date, trip.end_date)

    summary = AssistantItinerarySummary(
        title=_itinerary_title(trip),
        start_date=trip.start_date,
        end_date=trip.end_date,
        duration_days=duration_days,
        traveler_count=(requirements.adults + (requirements.minor_count or 0))
        if requirements is not None and requirements.adults is not None
        else None,
        cities=[],
        pace=None,
        cover_image=research.cover_image if research else None,
    )
    preview = AssistantItineraryPreview(
        id="generated-itinerary",
        title="Your AI-Generated Itinerary",
        itinerary_id=itinerary_id,
        summary=summary,
        days=day_previews,
    )
    sections = []
    if research is not None:
        places = [
            AssistantPlaceCard(
                id=item.id, name=item.name, location=item.location, image=item.image
            )
            for item in research.evidence
            if item.kind == "place"
        ]
        hotels = [
            item.hotel_card
            for item in research.evidence
            if item.hotel_card is not None
            and (item.expires_at is None or item.expires_at > utc_now())
        ]
        if places:
            sections.append(
                AssistantPlaceCarousel(
                    id="researched-places", title="Suggested for You", items=places[:5]
                )
            )
        if hotels:
            sections.append(
                AssistantHotelCarousel(
                    id="researched-hotels", title="Hotel Options", items=hotels[:5]
                )
            )
    sections.append(preview)
    return AssistantRichContent(sections=sections)


def _itinerary_title(trip: Trip) -> str:
    """Return a deterministic itinerary title."""
    if trip.title is not None:
        normalized_title = trip.title.strip()
        if normalized_title:
            return normalized_title

    return f"{trip.destination} Adventure"
