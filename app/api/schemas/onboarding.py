"""Backend-owned option catalogue for Flutter onboarding."""

from pydantic import BaseModel, ConfigDict


class OnboardingOptionResponse(BaseModel):
    """One stable selectable option and its display metadata."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    icon_key: str
    sort_order: int


class OnboardingOptionsResponse(BaseModel):
    """Versioned option groups rendered by Flutter."""

    version: int
    travel_styles: list[OnboardingOptionResponse]
    interests: list[OnboardingOptionResponse]
    budget_tiers: list[OnboardingOptionResponse]
    trip_paces: list[OnboardingOptionResponse]
    recommendation_scopes: list[OnboardingOptionResponse]
