"""Tests for destination View All pagination and detail coordination."""

import asyncio
import base64
import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import InvalidCursorError
from app.database.repositories.destinations import DestinationRepository
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.destinations import (
    DestinationCandidate,
    DestinationCollection,
    DestinationType,
    MediaAssetValue,
    RankedDestination,
)
from app.domain.errors import DestinationNotFoundError
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
    UserPreferenceSnapshot,
)
from app.domain.trips import CanonicalLocation
from app.services.destination_catalogue_service import DestinationCatalogueService


def candidate(slug: str, rank: int) -> DestinationCandidate:
    """Create one deterministic destination card."""

    return DestinationCandidate(
        id=uuid4(),
        slug=slug,
        name=slug.title(),
        destination_type=DestinationType.REGION,
        country_name="Pakistan",
        country_code="PK",
        summary="A sufficiently descriptive destination summary for testing.",
        full_description="A complete destination description for catalogue tests.",
        cover_image=MediaAssetValue(
            id=uuid4(),
            url=f"https://images.example.com/{slug}.jpg",
            alt_text=f"View of {slug}",
            caption=None,
            width=None,
            height=None,
        ),
        latitude=35.0,
        longitude=75.0,
        map_zoom=9,
        budget_tier=BudgetTier.MID_RANGE,
        styles=(TravelStyle.ADVENTURE,),
        interests=(TravelInterest.HIKING,),
        editorial_rank=rank,
        featured_rank=rank,
        popular_rank=rank,
    )


def preference() -> UserPreferenceSnapshot:
    """Create one complete local preference snapshot."""

    now = datetime(2026, 9, 10, tzinfo=UTC)
    return UserPreferenceSnapshot(
        user_id=uuid4(),
        travel_styles=(TravelStyle.ADVENTURE,),
        interests=(TravelInterest.HIKING,),
        budget_tier=BudgetTier.MID_RANGE,
        trip_pace=TripPace.BALANCED,
        recommendation_scope=RecommendationScope.LOCAL,
        home_location=CanonicalLocation(
            provider="google",
            provider_location_id="lahore-id",
            canonical_name="Lahore, Pakistan",
            country_code="PK",
            latitude=31.5204,
            longitude=74.3587,
        ),
        onboarding_completed_at=now,
        created_at=now,
        updated_at=now,
    )


def create_service() -> tuple[DestinationCatalogueService, Mock, Mock]:
    destinations = Mock(spec=DestinationRepository)
    rows = [
        RankedDestination(candidate(slug, rank), (-62, rank, slug))
        for slug, rank in [("skardu", 1), ("hunza", 2)]
    ]

    async def ranked(*, after=None, limit=20, **kwargs):
        return [row for row in rows if after is None or row.key > after][:limit]

    destinations.list_ranked = AsyncMock(side_effect=ranked)
    destinations.get_published_by_slug = AsyncMock()
    destinations.list_destination_media = AsyncMock(return_value=())
    destinations.list_published_places = AsyncMock(return_value=[])
    preferences = Mock(spec=UserPreferenceRepository)
    preferences.get_snapshot = AsyncMock(return_value=preference())
    service = DestinationCatalogueService(
        session=Mock(spec=AsyncSession),
        destination_repository=destinations,
        preference_repository=preferences,
    )
    return service, destinations, preferences


def test_suggested_view_all_uses_opaque_cursor_and_preferences() -> None:
    """Successive pages should preserve ranking context without client scoring."""

    service, _, preferences = create_service()
    first = asyncio.run(
        service.list_destinations(
            user_id=preference().user_id,
            collection=DestinationCollection.SUGGESTED,
            limit=1,
        )
    )
    second = asyncio.run(
        service.list_destinations(
            user_id=preference().user_id,
            collection=DestinationCollection.SUGGESTED,
            limit=1,
            cursor=first.next_cursor,
        )
    )

    assert [item.slug for item in first.items] == ["skardu"]
    assert [item.slug for item in second.items] == ["hunza"]
    assert second.next_cursor is None
    assert preferences.get_snapshot.await_count == 2


def test_cursor_cannot_be_reused_for_another_collection() -> None:
    """A suggested cursor must not silently page a popular collection."""

    service, _, _ = create_service()
    first = asyncio.run(
        service.list_destinations(
            user_id=preference().user_id,
            collection=DestinationCollection.SUGGESTED,
            limit=1,
        )
    )

    with pytest.raises(InvalidCursorError):
        asyncio.run(
            service.list_destinations(
                user_id=preference().user_id,
                collection=DestinationCollection.POPULAR,
                limit=1,
                cursor=first.next_cursor,
            )
        )


def test_missing_destination_raises_public_domain_error() -> None:
    """Unpublished and unknown slugs should share a safe not-found result."""

    service, destinations, _ = create_service()
    destinations.get_published_by_slug.return_value = None

    with pytest.raises(DestinationNotFoundError):
        asyncio.run(service.get_destination(slug="missing"))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        True,
        123,
        {},
        {"v": True, "context": "x", "key": [0, 1, "slug"]},
        {"v": 2, "context": "x", "key": [False, 1, "slug"]},
        {"v": 2, "context": "x", "key": [0, 1, 123]},
        {"v": 2, "context": "x", "key": [0, 1, {}]},
        {"v": 2, "context": "x", "key": [2**80, 1, "slug"]},
        {"v": 2, "context": "x", "key": [0, 2**80, "slug"]},
        {"v": 1, "context": "x", "item_id": 123},
    ],
)
def test_malformed_cursor_is_rejected_before_database_reads(payload) -> None:
    service, destinations, preferences = create_service()
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(InvalidCursorError):
        asyncio.run(
            service.list_destinations(
                user_id=uuid4(),
                collection=DestinationCollection.SUGGESTED,
                cursor=cursor,
            )
        )
    destinations.list_ranked.assert_not_awaited()
    preferences.get_snapshot.assert_not_awaited()


def test_preference_change_rejects_stale_cursor() -> None:
    service, destinations, preferences = create_service()
    first = asyncio.run(
        service.list_destinations(
            user_id=uuid4(),
            collection=DestinationCollection.SUGGESTED,
            limit=1,
        )
    )
    preferences.get_snapshot.return_value = replace(
        preferences.get_snapshot.return_value,
        interests=(TravelInterest.HISTORY,),
    )
    destinations.list_ranked.reset_mock()
    with pytest.raises(InvalidCursorError):
        asyncio.run(
            service.list_destinations(
                user_id=uuid4(),
                collection=DestinationCollection.SUGGESTED,
                cursor=first.next_cursor,
            )
        )
    destinations.list_ranked.assert_not_awaited()
