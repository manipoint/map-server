"""Tests for optimized preference persistence operations."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.user_preference import UserPreference
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
)


def create_session() -> Mock:
    """Create one async-session mock for repository tests."""

    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return session


def test_get_missing_preferences_uses_one_query_and_returns_defaults() -> None:
    """New-user bootstrap should not perform a second interest query."""

    user_id = uuid4()
    query_result = Mock()
    query_result.all.return_value = []
    session = create_session()
    session.execute.return_value = query_result
    repository = UserPreferenceRepository(session)

    snapshot = asyncio.run(repository.get_snapshot(user_id=user_id))

    assert snapshot.user_id == user_id
    assert snapshot.onboarding_completed is False
    assert snapshot.personalization_ready is False
    assert snapshot.interests == ()
    session.execute.assert_awaited_once()


def test_get_preferences_builds_sorted_snapshot_from_one_join() -> None:
    """One bounded join should hydrate scalar and normalized interest data."""

    user_id = uuid4()
    now = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
    preference = UserPreference(
        user_id=user_id,
        travel_style=TravelStyle.NATURE.value,
        budget_tier=BudgetTier.MID_RANGE.value,
        trip_pace=TripPace.BALANCED.value,
        recommendation_scope=RecommendationScope.BOTH.value,
        onboarding_completed_at=now,
        created_at=now,
        updated_at=now,
    )
    query_result = Mock()
    query_result.all.return_value = [
        (preference, TravelInterest.HIKING.value),
        (preference, TravelInterest.HISTORY.value),
    ]
    session = create_session()
    session.execute.return_value = query_result
    repository = UserPreferenceRepository(session)

    snapshot = asyncio.run(repository.get_snapshot(user_id=user_id))

    assert snapshot.travel_style is TravelStyle.NATURE
    assert snapshot.interests == (
        TravelInterest.HIKING,
        TravelInterest.HISTORY,
    )
    assert snapshot.personalization_ready is True
    session.execute.assert_awaited_once()


def test_replace_uses_upsert_and_bulk_interest_addition() -> None:
    """A complete onboarding save should be atomic and idempotent."""

    user_id = uuid4()
    now = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
    preference = UserPreference(
        user_id=user_id,
        travel_style=TravelStyle.NATURE.value,
        budget_tier=BudgetTier.MID_RANGE.value,
        trip_pace=TripPace.BALANCED.value,
        recommendation_scope=RecommendationScope.BOTH.value,
        onboarding_completed_at=now,
        created_at=now,
        updated_at=now,
    )
    upsert_result = Mock()
    upsert_result.scalar_one.return_value = preference
    session = create_session()
    session.execute.side_effect = [upsert_result, Mock()]
    repository = UserPreferenceRepository(session)

    snapshot = asyncio.run(
        repository.replace(
            user_id=user_id,
            travel_style=TravelStyle.NATURE,
            interests=(TravelInterest.HIKING, TravelInterest.HISTORY),
            budget_tier=BudgetTier.MID_RANGE,
            trip_pace=TripPace.BALANCED,
            recommendation_scope=RecommendationScope.BOTH,
            home_location=None,
            completed_at=now,
        )
    )

    assert snapshot.interests == (
        TravelInterest.HIKING,
        TravelInterest.HISTORY,
    )
    assert session.execute.await_count == 2
    upsert = session.execute.await_args_list[0].args[0]
    assert "coalesce" in str(upsert.compile()).lower()
    added = list(session.add_all.call_args.args[0])
    assert {item.interest for item in added} == {"hiking", "history"}
    session.flush.assert_awaited_once_with()


def test_skip_upsert_does_not_delete_existing_interests() -> None:
    """Skipping later must not erase selections already stored by the user."""

    session = create_session()
    repository = UserPreferenceRepository(session)

    asyncio.run(
        repository.mark_onboarding_skipped(
            user_id=uuid4(),
            completed_at=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        )
    )

    session.execute.assert_awaited_once()
    session.add_all.assert_not_called()
    session.flush.assert_awaited_once_with()
