"""Profile preferences used as defaults while planning a trip."""

from pydantic import BaseModel, ConfigDict, Field

from app.domain.preferences import (
    BudgetTier,
    TravelInterest,
    TravelStyle,
    TripPace,
)
from app.domain.value_objects import CountryCode


class PlanningPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    travel_styles: tuple[TravelStyle, ...] = Field(default=(), max_length=10)
    interests: tuple[TravelInterest, ...] = Field(default=(), max_length=10)
    budget_tier: BudgetTier | None = None
    trip_pace: TripPace | None = None
    home_city: str | None = Field(default=None, min_length=2, max_length=200)
    home_country_code: CountryCode | None = None
