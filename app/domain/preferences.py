"""User travel-preference domain values and snapshots."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.trips import CanonicalLocation


class TravelStyle(StrEnum):
    """Primary experience style used to personalize destinations."""

    BEACHES = "beaches"
    ADVENTURE = "adventure"
    FOOD = "food"
    LUXURY = "luxury"
    NATURE = "nature"
    CULTURE = "culture"


class BudgetTier(StrEnum):
    """Coarse budget band used for ranking rather than price quoting."""

    BUDGET = "budget"
    MID_RANGE = "mid_range"
    PREMIUM = "premium"
    LUXURY = "luxury"


class TripPace(StrEnum):
    """Preferred itinerary density."""

    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"


class RecommendationScope(StrEnum):
    """Geographic boundary for home recommendations."""

    LOCAL = "local"
    INTERNATIONAL = "international"
    BOTH = "both"


class TravelInterest(StrEnum):
    """Supported interests used for deterministic recommendation ranking."""

    HIKING = "hiking"
    PHOTOGRAPHY = "photography"
    NIGHTLIFE = "nightlife"
    WELLNESS = "wellness"
    HISTORY = "history"
    WILDLIFE = "wildlife"
    SHOPPING = "shopping"
    LOCAL_CULTURE = "local_culture"
    EVENTS = "events"


@dataclass(frozen=True, slots=True)
class UserPreferenceSnapshot:
    """Transport-independent view of one user's current preferences."""

    user_id: UUID
    travel_style: TravelStyle | None
    interests: tuple[TravelInterest, ...]
    budget_tier: BudgetTier | None
    trip_pace: TripPace | None
    recommendation_scope: RecommendationScope
    home_location: CanonicalLocation | None
    onboarding_completed_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None

    @property
    def onboarding_completed(self) -> bool:
        """Report whether onboarding was completed or explicitly skipped."""

        return self.onboarding_completed_at is not None

    @property
    def personalization_ready(self) -> bool:
        """Report whether enough choices exist for personalized ranking."""

        return (
            self.travel_style is not None
            and bool(self.interests)
            and self.budget_tier is not None
            and self.trip_pace is not None
        )
