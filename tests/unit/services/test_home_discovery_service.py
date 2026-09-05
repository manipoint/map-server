"""Tests for deterministic and provider-free Home ranking."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.destinations import DestinationRepository
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.destinations import DestinationCandidate, DiscoveryCollectionKind
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
    UserPreferenceSnapshot,
)
from app.domain.trips import CanonicalLocation
from app.services.home_discovery_service import HomeDiscoveryService


def candidate(
    slug: str,
    *,
    country_code: str,
    budget: BudgetTier,
    styles: tuple[TravelStyle, ...],
    interests: tuple[TravelInterest, ...],
    editorial_rank: int,
    featured_rank: int | None = None,
    popular_rank: int | None = None,
) -> DestinationCandidate:
    """Create one deterministic catalogue candidate."""

    return DestinationCandidate(
        id=uuid4(),
        slug=slug,
        name=slug.title(),
        country_name="Pakistan" if country_code == "PK" else "France",
        country_code=country_code,
        summary="A sufficiently descriptive destination summary for the Home card.",
        image_url=f"https://images.example.com/{slug}.jpg",
        image_alt=f"View of {slug}",
        latitude=31.5,
        longitude=74.3,
        budget_tier=budget,
        styles=styles,
        interests=interests,
        editorial_rank=editorial_rank,
        featured_rank=featured_rank,
        popular_rank=popular_rank,
    )


def preference(
    *,
    scope: RecommendationScope = RecommendationScope.BOTH,
    completed: bool = True,
) -> UserPreferenceSnapshot:
    """Create personalized or skipped preference state."""

    now = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    return UserPreferenceSnapshot(
        user_id=uuid4(),
        travel_style=TravelStyle.NATURE if completed else None,
        interests=(TravelInterest.HIKING,) if completed else (),
        budget_tier=BudgetTier.MID_RANGE if completed else None,
        trip_pace=TripPace.BALANCED if completed else None,
        recommendation_scope=scope,
        home_location=(
            CanonicalLocation(
                provider="google",
                provider_location_id="lahore-id",
                canonical_name="Lahore, Pakistan",
                country_code="PK",
                latitude=31.5204,
                longitude=74.3587,
            )
            if scope is not RecommendationScope.BOTH
            else None
        ),
        onboarding_completed_at=now,
        created_at=now,
        updated_at=now,
    )


def create_service(
    *,
    snapshot: UserPreferenceSnapshot,
    catalogue: list[DestinationCandidate],
) -> tuple[HomeDiscoveryService, Mock, Mock]:
    """Create a service with isolated database repositories."""

    destinations = Mock(spec=DestinationRepository)
    destinations.list_published_catalog = AsyncMock(return_value=catalogue)
    preferences = Mock(spec=UserPreferenceRepository)
    preferences.get_snapshot = AsyncMock(return_value=snapshot)
    session = Mock(spec=AsyncSession)
    return (
        HomeDiscoveryService(
            session=session,
            destination_repository=destinations,
            preference_repository=preferences,
        ),
        destinations,
        preferences,
    )


def catalogue() -> list[DestinationCandidate]:
    """Return candidates that exercise all ranking inputs."""

    return [
        candidate(
            "hunza",
            country_code="PK",
            budget=BudgetTier.MID_RANGE,
            styles=(TravelStyle.NATURE,),
            interests=(TravelInterest.HIKING,),
            editorial_rank=2,
            featured_rank=2,
            popular_rank=2,
        ),
        candidate(
            "lahore",
            country_code="PK",
            budget=BudgetTier.BUDGET,
            styles=(TravelStyle.CULTURE,),
            interests=(TravelInterest.HISTORY,),
            editorial_rank=1,
            popular_rank=1,
        ),
        candidate(
            "paris",
            country_code="FR",
            budget=BudgetTier.PREMIUM,
            styles=(TravelStyle.CULTURE,),
            interests=(TravelInterest.HIKING,),
            editorial_rank=3,
            featured_rank=1,
        ),
    ]


def test_personalized_ranking_uses_fixed_weights_and_curated_sections() -> None:
    """Best preference match should lead while editorial sections keep ranks."""

    snapshot = preference()
    service, destinations, preferences = create_service(
        snapshot=snapshot,
        catalogue=catalogue(),
    )

    result = asyncio.run(service.get_home(user_id=snapshot.user_id))

    assert [item.slug for item in result.suggested] == ["hunza", "paris", "lahore"]
    assert [item.slug for item in result.popular] == ["lahore", "hunza"]
    assert [item.slug for item in result.spotlight] == ["paris", "hunza"]
    assert result.spotlight_kind is DiscoveryCollectionKind.FEATURED
    destinations.list_published_catalog.assert_awaited_once_with()
    preferences.get_snapshot.assert_awaited_once_with(user_id=snapshot.user_id)


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (RecommendationScope.LOCAL, ["hunza", "lahore"]),
        (RecommendationScope.INTERNATIONAL, ["paris"]),
    ],
)
def test_suggestions_respect_explicit_home_country_scope(
    scope: RecommendationScope,
    expected: list[str],
) -> None:
    """Budget must never override the user's geographic recommendation scope."""

    snapshot = preference(scope=scope)
    service, _, _ = create_service(snapshot=snapshot, catalogue=catalogue())

    result = asyncio.run(service.get_home(user_id=snapshot.user_id))

    assert [item.slug for item in result.suggested] == expected


def test_skipped_onboarding_returns_general_curated_sections() -> None:
    """Skipped users should see content without fake personalization."""

    snapshot = preference(completed=False)
    service, _, _ = create_service(snapshot=snapshot, catalogue=catalogue())

    result = asyncio.run(service.get_home(user_id=snapshot.user_id))

    assert result.personalization_ready is False
    assert result.suggested == ()
    assert result.popular
    assert result.spotlight_kind is DiscoveryCollectionKind.FEATURED


def test_home_limit_bounds_every_section() -> None:
    """A small requested limit should consistently bound response work."""

    snapshot = preference()
    service, _, _ = create_service(snapshot=snapshot, catalogue=catalogue())

    result = asyncio.run(service.get_home(user_id=snapshot.user_id, section_limit=1))

    assert len(result.suggested) == 1
    assert len(result.popular) == 1
    assert len(result.spotlight) == 1


@pytest.mark.parametrize("limit", [0, 7])
def test_home_rejects_unbounded_section_limit(limit: int) -> None:
    """Internal callers cannot bypass the public six-card section cap."""

    snapshot = preference()
    service, destinations, preferences = create_service(
        snapshot=snapshot,
        catalogue=catalogue(),
    )

    with pytest.raises(ValueError, match="between 1 and 6"):
        asyncio.run(service.get_home(user_id=snapshot.user_id, section_limit=limit))

    destinations.list_published_catalog.assert_not_awaited()
    preferences.get_snapshot.assert_not_awaited()
