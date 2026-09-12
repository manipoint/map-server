"""Public schemas for onboarding and travel preferences."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
    UserPreferenceSnapshot,
)
from app.domain.trips import CanonicalLocation


class UserPreferenceUpdateRequest(BaseModel):
    """Complete preference selection submitted at onboarding completion."""

    model_config = ConfigDict(extra="forbid")

    travel_styles: list[TravelStyle] = Field(min_length=1, max_length=3)
    interests: list[TravelInterest] = Field(min_length=1, max_length=5)
    budget_tier: BudgetTier
    trip_pace: TripPace
    recommendation_scope: RecommendationScope = RecommendationScope.BOTH
    home_location: CanonicalLocation | None = None

    @field_validator("interests")
    @classmethod
    def reject_duplicate_interests(
        cls,
        values: list[TravelInterest],
    ) -> list[TravelInterest]:
        """Reject ambiguous repeated choices instead of silently changing input."""

        if len(values) != len(set(values)):
            raise ValueError("interests must not contain duplicates")
        return values

    @field_validator("travel_styles")
    @classmethod
    def reject_duplicate_styles(
        cls,
        values: list[TravelStyle],
    ) -> list[TravelStyle]:
        """Reject repeated style IDs from stale or malformed clients."""

        if len(values) != len(set(values)):
            raise ValueError("travel_styles must not contain duplicates")
        return values

    @model_validator(mode="after")
    def require_home_for_geographic_scope(self) -> Self:
        """Require a home country when local/international filtering is requested."""

        if (
            self.recommendation_scope
            in {RecommendationScope.LOCAL, RecommendationScope.INTERNATIONAL}
            and self.home_location is None
        ):
            raise ValueError(
                "home_location is required for local or international scope"
            )
        return self


class UserPreferenceResponse(BaseModel):
    """Flutter-ready preference and onboarding state."""

    model_config = ConfigDict(extra="forbid")

    travel_styles: list[TravelStyle]
    interests: list[TravelInterest]
    budget_tier: BudgetTier | None
    trip_pace: TripPace | None
    recommendation_scope: RecommendationScope
    home_location: CanonicalLocation | None
    onboarding_completed: bool
    personalization_ready: bool
    onboarding_completed_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_snapshot(
        cls,
        snapshot: UserPreferenceSnapshot,
    ) -> "UserPreferenceResponse":
        """Create a public response without leaking persistence ownership fields."""

        return cls(
            travel_styles=list(snapshot.travel_styles),
            interests=list(snapshot.interests),
            budget_tier=snapshot.budget_tier,
            trip_pace=snapshot.trip_pace,
            recommendation_scope=snapshot.recommendation_scope,
            home_location=snapshot.home_location,
            onboarding_completed=snapshot.onboarding_completed,
            personalization_ready=snapshot.personalization_ready,
            onboarding_completed_at=snapshot.onboarding_completed_at,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
        )
