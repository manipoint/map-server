"""Tests for preference transaction boundaries."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
    UserPreferenceSnapshot,
)
from app.services.user_preference_service import UserPreferenceService


def create_dependencies() -> tuple[Mock, Mock]:
    """Create mocked preference persistence dependencies."""

    session = Mock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    repository = Mock(spec=UserPreferenceRepository)
    repository.get_snapshot = AsyncMock()
    repository.replace = AsyncMock()
    repository.mark_onboarding_skipped = AsyncMock()
    return session, repository


def create_snapshot(*, completed: bool = True) -> UserPreferenceSnapshot:
    """Create one service result with deterministic values."""

    now = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    return UserPreferenceSnapshot(
        user_id=uuid4(),
        travel_style=TravelStyle.NATURE if completed else None,
        interests=(TravelInterest.HIKING,) if completed else (),
        budget_tier=BudgetTier.MID_RANGE if completed else None,
        trip_pace=TripPace.BALANCED if completed else None,
        recommendation_scope=RecommendationScope.BOTH,
        home_location=None,
        onboarding_completed_at=now if completed else None,
        created_at=now if completed else None,
        updated_at=now if completed else None,
    )


def test_get_preferences_is_read_only() -> None:
    """A home/bootstrap read should not open a write transaction."""

    session, repository = create_dependencies()
    snapshot = create_snapshot()
    repository.get_snapshot.return_value = snapshot
    service = UserPreferenceService(session=session, repository=repository)

    result = asyncio.run(service.get_preferences(user_id=snapshot.user_id))

    assert result is snapshot
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_complete_onboarding_deduplicates_and_orders_interests() -> None:
    """Service persistence should be deterministic for cache-friendly responses."""

    session, repository = create_dependencies()
    snapshot = create_snapshot()
    repository.replace.return_value = snapshot
    service = UserPreferenceService(session=session, repository=repository)

    result = asyncio.run(
        service.complete_onboarding(
            user_id=snapshot.user_id,
            travel_style=TravelStyle.NATURE,
            interests=[
                TravelInterest.HISTORY,
                TravelInterest.HIKING,
                TravelInterest.HISTORY,
            ],
            budget_tier=BudgetTier.MID_RANGE,
            trip_pace=TripPace.BALANCED,
            recommendation_scope=RecommendationScope.BOTH,
            home_location=None,
        )
    )

    assert result is snapshot
    assert repository.replace.await_args.kwargs["interests"] == (
        TravelInterest.HIKING,
        TravelInterest.HISTORY,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_complete_onboarding_rolls_back_failure() -> None:
    """Partial scalar and interest writes must never be committed."""

    session, repository = create_dependencies()
    repository.replace.side_effect = RuntimeError("write failed")
    service = UserPreferenceService(session=session, repository=repository)

    with pytest.raises(RuntimeError, match="write failed"):
        asyncio.run(
            service.complete_onboarding(
                user_id=uuid4(),
                travel_style=TravelStyle.NATURE,
                interests=[TravelInterest.HIKING],
                budget_tier=BudgetTier.MID_RANGE,
                trip_pace=TripPace.BALANCED,
                recommendation_scope=RecommendationScope.BOTH,
                home_location=None,
            )
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_skip_reads_current_snapshot_then_commits() -> None:
    """Skip should build its response inside the successful transaction."""

    session, repository = create_dependencies()
    snapshot = create_snapshot(completed=False)
    repository.get_snapshot.return_value = snapshot
    service = UserPreferenceService(session=session, repository=repository)

    result = asyncio.run(service.skip_onboarding(user_id=snapshot.user_id))

    assert result is snapshot
    repository.mark_onboarding_skipped.assert_awaited_once()
    session.commit.assert_awaited_once_with()
    repository.get_snapshot.assert_awaited_once_with(user_id=snapshot.user_id)


def test_skip_rolls_back_when_snapshot_read_fails() -> None:
    """A response-hydration failure must not leave skipped state committed."""

    session, repository = create_dependencies()
    repository.get_snapshot.side_effect = RuntimeError("read failed")
    service = UserPreferenceService(session=session, repository=repository)

    with pytest.raises(RuntimeError, match="read failed"):
        asyncio.run(service.skip_onboarding(user_id=uuid4()))

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
