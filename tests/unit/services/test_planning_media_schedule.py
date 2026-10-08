"""Verified catalogue media and schedule data survive planning and delivery."""

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.database.models.trip import Trip
from app.domain.assistant_content import AssistantRichContent
from app.domain.destinations import MediaAssetValue
from app.domain.media import AssistantMedia
from app.domain.trip_requirements import TripRequirements
from app.graph.planning_builder import validate_researched_itinerary
from app.graph.planning_schemas import ResearchedItinerary
from app.services.assistant_rich_content_mapper import build_itinerary_rich_content
from app.services.planning_research_service import (
    PlanningResearch,
    PlanningResearchService,
    ResearchEvidence,
    _catalogue_image,
)


def media():
    return MediaAssetValue(
        uuid4(), "https://images.example.com/hunza.jpg", "Hunza valley", None, 800, 600
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "http://images.example.com/photo.jpg"},
        {"width": None},
        {"url": "invalid"},
    ],
)
def test_invalid_catalogue_media_is_omitted_without_blocking_planning(changes):
    assert _catalogue_image(replace(media(), **changes)) is None


@pytest.mark.parametrize(
    "matched,has_places", [(True, True), (True, False), (False, False)]
)
def test_catalogue_lookup_reuses_active_media_and_skips_redundant_search(
    monkeypatch, matched, has_places
):
    asset = media()
    candidate = SimpleNamespace(id=uuid4(), name="Hunza", cover_image=asset)
    place = SimpleNamespace(
        id=uuid4(),
        name="Fort",
        address=None,
        summary="A curated historic fort",
        cover_image=asset,
    )
    repository = AsyncMock()
    repository.find_for_planning.return_value = candidate if matched else None
    repository.list_published_places.return_value = [place] if has_places else []
    monkeypatch.setattr(
        "app.services.planning_research_service.DestinationRepository",
        lambda session: repository,
    )
    session = AsyncMock()
    factory = Mock(return_value=session)
    client = AsyncMock()
    service = PlanningResearchService(
        client=client,
        places_available=False,
        hotels_available=False,
        round_trip_flights_available=False,
        session_factory=factory,
    )
    requirements = TripRequirements(
        destination="Hunza",
        start_date=date(2099, 11, 7),
        duration_days=2,
        adults=2,
        minor_count=0,
        transport="own_arrangements",
        needs_lodging=False,
    )
    result = asyncio.run(service.research(requirements))
    assert (result.cover_image is not None) is matched
    assert len(result.evidence) == int(has_places)
    if has_places:
        assert result.evidence[0].image.url == result.cover_image.url
        assert result.evidence[0].source_id == str(place.id)
        assert result.warnings == ()
    else:
        assert len(result.warnings) == 1
    client.search_places.assert_not_awaited()
    session.__aexit__.assert_awaited_once()


def test_schedule_uses_evidence_images_preserves_offsets_and_restores_rich_content():
    verified = AssistantMedia(
        url="https://images.example.com/fort.jpg", alt_text="Fort"
    )
    research = PlanningResearch(
        searched_at=datetime.now(UTC),
        time_zone="Asia/Karachi",
        cover_image=verified,
        evidence=(
            ResearchEvidence(
                id="place-1",
                kind="place",
                name="Fort",
                location="Hunza",
                description="Verified historic fort",
                image=verified,
            ),
        ),
    )
    requirements = TripRequirements(
        destination="Hunza", start_date=date(2099, 11, 7), duration_days=2
    )
    generated = ResearchedItinerary.model_validate(
        {
            "summary": "Draft",
            "items": [
                {
                    "day_number": 1,
                    "item_type": "place",
                    "title": "Untrusted name",
                    "evidence_id": "place-1",
                    "starts_at": "2099-11-07T09:00:00+05:00",
                    "ends_at": "2099-11-07T10:00:00+05:00",
                    "image": None,
                },
                {
                    "day_number": 1,
                    "item_type": "meal",
                    "title": "Lunch",
                    "starts_at": "2099-11-07T12:00:00+05:00",
                    "ends_at": "2099-11-07T13:00:00+05:00",
                },
                {
                    "day_number": 2,
                    "item_type": "activity",
                    "title": "Explore",
                    "image": None,
                    "starts_at": "2099-11-08T10:00:00+05:00",
                    "ends_at": "2099-11-08T15:00:00+05:00",
                },
            ],
        }
    )
    normalized = validate_researched_itinerary(
        generated, requirements=requirements, research=research
    )
    assert normalized.items[0].image == verified
    assert normalized.items[2].image is None
    trip = Trip(
        id=uuid4(),
        user_id=uuid4(),
        destination="Hunza",
        start_date=date(2099, 11, 7),
        end_date=date(2099, 11, 8),
        status="draft",
    )
    content = build_itinerary_rich_content(
        itinerary_id=uuid4(), trip=trip, generated=normalized, research=research
    )
    restored = AssistantRichContent.model_validate_json(content.model_dump_json())
    assert restored == content
    assert restored.sections[0].items[0].image == verified
    preview = restored.sections[-1]
    assert preview.summary.cover_image == verified
    assert [item.position for item in preview.days[0].activities] == [1, 2]
    first = preview.days[0].activities[0]
    assert first.title == "Fort"
    assert first.image == verified
    assert first.starts_at.isoformat() == "2099-11-07T09:00:00+05:00"
    assert (
        preview.days[1].activities[0].starts_at.isoformat()
        == "2099-11-08T10:00:00+05:00"
    )
    assert (
        preview.days[1].activities[0].ends_at.isoformat() == "2099-11-08T15:00:00+05:00"
    )
