"""Validation and normalization of model-generated itineraries."""

from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.common.time import utc_now
from app.domain.itineraries import MAX_ITINERARY_ITEMS, ItineraryItemType
from app.domain.trip_requirements import TripRequirements
from app.domain.trip_rules import inclusive_day_count
from app.graph.planning.itinerary_templates import generic_activity_text
from app.graph.planning_schemas import ResearchedItinerary
from app.graph.schemas.itineraries import GeneratedItinerary
from app.services.planning_research_service import PlanningResearch
from app.services.planning_weather import itinerary_weather_notes


def validate_researched_itinerary(
    generated: ResearchedItinerary,
    *,
    requirements: TripRequirements,
    research: PlanningResearch,
    language: str = "en",
    required_evidence_ids: set[str] | None = None,
) -> GeneratedItinerary:
    start = requirements.start_date
    end = requirements.resolved_end_date
    if start is None or end is None:
        raise ValueError("Itinerary requires complete dates")
    days = inclusive_day_count(start, end)
    if {item.day_number for item in generated.items} != set(range(1, days + 1)):
        raise ValueError(
            "Itinerary must cover every requested day exactly by day number"
        )
    evidence = {item.id: item for item in research.evidence}
    previous_end = None
    previous_day = 0
    normalized = []
    for item in generated.items:
        if item.day_number < previous_day:
            raise ValueError("Itinerary items must be ordered by day")
        previous_day = item.day_number
        expected_date = start + timedelta(days=item.day_number - 1)
        if (item.starts_at is None) != (item.ends_at is None):
            raise ValueError("Scheduled items require both start and end times")
        values = item.model_dump(exclude={"evidence_id", "generic_activity"})
        # A model can select evidence but cannot supply its own image URLs.
        values["image"] = None
        values["start_time_zone"] = research.time_zone
        values["end_time_zone"] = research.time_zone
        if item.evidence_id is not None:
            source = evidence.get(item.evidence_id)
            if source is None or (source.expires_at and source.expires_at <= utc_now()):
                raise ValueError("Itinerary references missing or expired evidence")
            if item.item_type.value != source.kind:
                raise ValueError("Itinerary evidence type does not match item type")
            # Display names come from trusted normalized results, never the model.
            values.update(
                title=source.name,
                location_name=source.location,
                description=source.description,
                starts_at=(
                    source.starts_at if source.kind == "flight" else item.starts_at
                ),
                ends_at=source.ends_at if source.kind == "flight" else item.ends_at,
                start_time_zone=(
                    source.start_time_zone
                    if source.kind == "flight"
                    else research.time_zone
                ),
                end_time_zone=(
                    source.end_time_zone
                    if source.kind == "flight"
                    else research.time_zone
                ),
                image=source.image
                or (source.hotel_card.image if source.hotel_card else None),
            )
        elif item.item_type in {
            ItineraryItemType.PLACE,
            ItineraryItemType.HOTEL,
            ItineraryItemType.FLIGHT,
        }:
            raise ValueError("Named place, hotel and flight items require evidence IDs")
        else:
            code = item.generic_activity or {
                "meal": "meal",
                "transfer": "transfer",
                "note": "note",
            }.get(item.item_type.value, "free_time")

            title, description = generic_activity_text(code=code, language=language)
            values.update(title=title, description=description, location_name=None)
        starts, ends = values["starts_at"], values["ends_at"]
        if item.item_type != ItineraryItemType.FLIGHT:
            if research.time_zone is None:
                values.update(starts_at=None, ends_at=None)
                starts = ends = None
            elif starts is None and item.item_type != ItineraryItemType.NOTE:
                raise ValueError(
                    "Every activity requires start/end times in the verified destination timezone"
                )
            elif starts is not None:
                try:
                    zone = ZoneInfo(research.time_zone)
                except ZoneInfoNotFoundError as error:
                    raise ValueError("Research time zone is unavailable") from error
                for value in (starts, ends):
                    if value.utcoffset() != value.astimezone(zone).utcoffset():
                        raise ValueError(
                            "Scheduled offset does not match destination time zone"
                        )
                if ends.date() != expected_date:
                    raise ValueError("Activity must finish on its trip day")
        if starts is not None:
            if starts.date() != expected_date or ends is None or ends <= starts:
                raise ValueError(
                    "Scheduled item must match its trip day and time order"
                )
            if previous_end is not None and starts < previous_end:
                raise ValueError(
                    "Scheduled itinerary items overlap or are out of order"
                )
            previous_end = ends
        normalized.append(values)
    if required_evidence_ids:
        referenced_ids = {item.evidence_id for item in generated.items}
        if not required_evidence_ids.issubset(referenced_ids):
            raise ValueError(
                "Itinerary must include the selected flight and hotel options"
            )
    if research.time_zone is not None and {
        item["day_number"] for item in normalized if item["starts_at"] is not None
    } != set(range(1, days + 1)):
        raise ValueError("Each trip day requires at least one scheduled activity")
    weather_notes = (
        itinerary_weather_notes(
            start_date=start,
            end_date=end,
            forecast=research.weather_forecast,
            language=language,
        )
        if research.weather_requested or research.weather_forecast is not None
        else []
    )
    if len(normalized) + len(weather_notes) > MAX_ITINERARY_ITEMS:
        raise ValueError(
            "Too many schedule items; reserve one weather note per trip day"
        )
    normalized.extend(note.model_dump() for note in weather_notes)
    normalized.sort(key=lambda item: item["day_number"])
    return GeneratedItinerary(
        summary=(
            "Aap ki safar ki zarooriyat par mabni draft plan. Activity aur transfer ke auqat andazan hain."
            if language == "ur-Latn"
            else "Draft itinerary based on your trip requirements. Activity and transfer times are estimates."
        ),
        items=normalized,
    )
