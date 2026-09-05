"""Tests for stable preference domain values."""

from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
)


def test_preference_enums_expose_stable_api_values() -> None:
    """Flutter and PostgreSQL should share explicit string contracts."""

    assert {item.value for item in TravelStyle} == {
        "beaches",
        "adventure",
        "food",
        "luxury",
        "nature",
        "culture",
    }
    assert {item.value for item in BudgetTier} == {
        "budget",
        "mid_range",
        "premium",
        "luxury",
    }
    assert {item.value for item in TripPace} == {
        "relaxed",
        "balanced",
        "packed",
    }
    assert {item.value for item in RecommendationScope} == {
        "local",
        "international",
        "both",
    }
    assert len(TravelInterest) == 9
