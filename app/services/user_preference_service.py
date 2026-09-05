"""User onboarding and travel-preference use cases."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time import utc_now
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
    UserPreferenceSnapshot,
)
from app.domain.trips import CanonicalLocation


class UserPreferenceService:
    """Coordinate preference reads and transaction-safe onboarding writes."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: UserPreferenceRepository | None = None,
    ) -> None:
        self.session = session
        self.preferences = repository or UserPreferenceRepository(session)

    async def get_preferences(self, *, user_id: UUID) -> UserPreferenceSnapshot:
        """Return current preferences without external-provider or LLM calls."""

        return await self.preferences.get_snapshot(user_id=user_id)

    async def complete_onboarding(
        self,
        *,
        user_id: UUID,
        travel_style: TravelStyle,
        interests: list[TravelInterest],
        budget_tier: BudgetTier,
        trip_pace: TripPace,
        recommendation_scope: RecommendationScope,
        home_location: CanonicalLocation | None,
    ) -> UserPreferenceSnapshot:
        """Persist a complete onboarding selection and return its snapshot."""

        normalized_interests = tuple(
            sorted(set(interests), key=lambda item: item.value)
        )
        try:
            snapshot = await self.preferences.replace(
                user_id=user_id,
                travel_style=travel_style,
                interests=normalized_interests,
                budget_tier=budget_tier,
                trip_pace=trip_pace,
                recommendation_scope=recommendation_scope,
                home_location=home_location,
                completed_at=utc_now(),
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return snapshot

    async def skip_onboarding(self, *, user_id: UUID) -> UserPreferenceSnapshot:
        """Complete onboarding with defaults while preserving any saved choices."""

        try:
            await self.preferences.mark_onboarding_skipped(
                user_id=user_id,
                completed_at=utc_now(),
            )
            snapshot = await self.preferences.get_snapshot(user_id=user_id)
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return snapshot
