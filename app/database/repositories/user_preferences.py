"""Persistence operations for normalized user travel preferences."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.user_preference import UserInterest, UserPreference
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
    UserPreferenceSnapshot,
)
from app.domain.trips import CanonicalLocation


class UserPreferenceRepository:
    """Read and atomically replace authenticated-user preferences."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_snapshot(self, *, user_id: UUID) -> UserPreferenceSnapshot:
        """Return one preference snapshot using one bounded join query."""

        result = await self.session.execute(
            select(UserPreference, UserInterest.interest)
            .outerjoin(
                UserInterest,
                UserInterest.user_id == UserPreference.user_id,
            )
            .where(UserPreference.user_id == user_id)
            .order_by(UserInterest.interest)
        )
        rows = result.all()
        if not rows:
            return self.empty_snapshot(user_id=user_id)

        preference = rows[0][0]
        interests = tuple(
            TravelInterest(interest) for _, interest in rows if interest is not None
        )
        return self._snapshot(preference=preference, interests=interests)

    async def replace(
        self,
        *,
        user_id: UUID,
        travel_style: TravelStyle,
        interests: tuple[TravelInterest, ...],
        budget_tier: BudgetTier,
        trip_pace: TripPace,
        recommendation_scope: RecommendationScope,
        home_location: CanonicalLocation | None,
        completed_at: datetime,
    ) -> UserPreferenceSnapshot:
        """Upsert scalar choices and replace normalized interests."""

        location_values = self._location_values(home_location)
        values: dict[str, object | None] = {
            "user_id": user_id,
            "travel_style": travel_style.value,
            "budget_tier": budget_tier.value,
            "trip_pace": trip_pace.value,
            "recommendation_scope": recommendation_scope.value,
            "onboarding_completed_at": completed_at,
            **location_values,
        }
        statement = insert(UserPreference).values(**values)
        update_values = {
            name: value
            for name, value in values.items()
            if name not in {"user_id", "onboarding_completed_at"}
        }
        statement = statement.on_conflict_do_update(
            index_elements=[UserPreference.user_id],
            set_={
                **update_values,
                "onboarding_completed_at": func.coalesce(
                    UserPreference.onboarding_completed_at,
                    completed_at,
                ),
                "updated_at": func.now(),
            },
        ).returning(UserPreference)
        preference = (await self.session.execute(statement)).scalar_one()

        await self.session.execute(
            delete(UserInterest).where(UserInterest.user_id == user_id)
        )
        if interests:
            self.session.add_all(
                [
                    UserInterest(user_id=user_id, interest=interest.value)
                    for interest in interests
                ]
            )
        await self.session.flush()
        return self._snapshot(preference=preference, interests=interests)

    async def mark_onboarding_skipped(
        self,
        *,
        user_id: UUID,
        completed_at: datetime,
    ) -> None:
        """Mark onboarding complete without erasing previously saved choices."""

        statement = insert(UserPreference).values(
            user_id=user_id,
            recommendation_scope=RecommendationScope.BOTH.value,
            onboarding_completed_at=completed_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[UserPreference.user_id],
            set_={
                "onboarding_completed_at": func.coalesce(
                    UserPreference.onboarding_completed_at,
                    completed_at,
                ),
                "updated_at": func.now(),
            },
        )
        await self.session.execute(statement)
        await self.session.flush()

    @staticmethod
    def empty_snapshot(*, user_id: UUID) -> UserPreferenceSnapshot:
        """Return the stable API state for a user without a preference row."""

        return UserPreferenceSnapshot(
            user_id=user_id,
            travel_style=None,
            interests=(),
            budget_tier=None,
            trip_pace=None,
            recommendation_scope=RecommendationScope.BOTH,
            home_location=None,
            onboarding_completed_at=None,
            created_at=None,
            updated_at=None,
        )

    @staticmethod
    def _snapshot(
        *,
        preference: UserPreference,
        interests: tuple[TravelInterest, ...],
    ) -> UserPreferenceSnapshot:
        """Map persistence fields to enum-safe domain output."""

        return UserPreferenceSnapshot(
            user_id=preference.user_id,
            travel_style=(
                TravelStyle(preference.travel_style)
                if preference.travel_style is not None
                else None
            ),
            interests=interests,
            budget_tier=(
                BudgetTier(preference.budget_tier)
                if preference.budget_tier is not None
                else None
            ),
            trip_pace=(
                TripPace(preference.trip_pace)
                if preference.trip_pace is not None
                else None
            ),
            recommendation_scope=RecommendationScope(preference.recommendation_scope),
            home_location=preference.home_location,
            onboarding_completed_at=preference.onboarding_completed_at,
            created_at=preference.created_at,
            updated_at=preference.updated_at,
        )

    @staticmethod
    def _location_values(
        location: CanonicalLocation | None,
    ) -> dict[str, object | None]:
        """Flatten an optional canonical home location atomically."""

        if location is None:
            return {
                "home_location_provider": None,
                "home_provider_location_id": None,
                "home_canonical_name": None,
                "home_country_code": None,
                "home_latitude": None,
                "home_longitude": None,
            }
        return {
            "home_location_provider": location.provider,
            "home_provider_location_id": location.provider_location_id,
            "home_canonical_name": location.canonical_name,
            "home_country_code": location.country_code,
            "home_latitude": location.latitude,
            "home_longitude": location.longitude,
        }
