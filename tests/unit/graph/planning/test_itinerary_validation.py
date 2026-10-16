"""Focused tests for itinerary validation and trusted normalization."""

from datetime import UTC, date, datetime

import pytest

from app.domain.trip_requirements import TripRequirements
from app.graph.planning.itinerary_validation import validate_researched_itinerary
from app.graph.planning_schemas import ResearchedItinerary
from app.services.planning_research_service import PlanningResearch, ResearchEvidence


def requirements() -> TripRequirements:
    return TripRequirements(
        destination="Tokyo",
        start_date=date(2099, 11, 7),
        duration_days=2,
        adults=1,
        minor_count=0,
        transport="own_arrangements",
        needs_lodging=False,
        budget_decision="undecided",
    )


def proposal(**item_updates: object) -> ResearchedItinerary:
    return ResearchedItinerary.model_validate(
        {
            "summary": "Untrusted model summary",
            "items": [
                {
                    "day_number": 1,
                    "item_type": "activity",
                    "title": "Untrusted model title",
                    "generic_activity": "free_time",
                    **item_updates,
                },
                {
                    "day_number": 2,
                    "item_type": "activity",
                    "title": "Another untrusted title",
                    "generic_activity": "free_time",
                },
            ],
        }
    )


def research(*evidence: ResearchEvidence) -> PlanningResearch:
    return PlanningResearch(
        searched_at=datetime.now(UTC),
        evidence=evidence,
    )


def test_generic_activity_uses_server_owned_text_and_summary() -> None:
    result = validate_researched_itinerary(
        proposal(),
        requirements=requirements(),
        research=research(),
    )

    assert result.summary.startswith("Draft itinerary")
    assert result.items[0].title == "Free time"
    assert result.items[0].description == ("Choose an activity that suits your group.")


def test_named_place_requires_current_evidence() -> None:
    generated = proposal(item_type="place", evidence_id="place-1")

    with pytest.raises(ValueError, match="missing or expired evidence"):
        validate_researched_itinerary(
            generated,
            requirements=requirements(),
            research=research(),
        )


def test_trusted_evidence_overrides_model_display_values() -> None:
    source = ResearchEvidence(
        id="place-1",
        kind="place",
        name="Verified place",
        location="Tokyo",
        description="Verified description",
    )

    result = validate_researched_itinerary(
        proposal(item_type="place", evidence_id="place-1"),
        requirements=requirements(),
        research=research(source),
    )

    assert result.items[0].title == "Verified place"
    assert result.items[0].location_name == "Tokyo"
    assert result.items[0].description == "Verified description"


def test_scheduled_item_requires_both_start_and_end_times() -> None:
    generated = proposal(starts_at="2099-11-07T09:00:00Z")

    with pytest.raises(ValueError, match="both start and end times"):
        validate_researched_itinerary(
            generated,
            requirements=requirements(),
            research=research(),
        )
