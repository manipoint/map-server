"""Tests for public onboarding preference schemas."""

import pytest
from pydantic import ValidationError

from app.api.schemas.user_preferences import UserPreferenceUpdateRequest


def valid_payload() -> dict[str, object]:
    """Return one valid personalized onboarding payload."""

    return {
        "travel_styles": ["nature", "adventure"],
        "interests": ["hiking", "history"],
        "budget_tier": "mid_range",
        "trip_pace": "balanced",
        "recommendation_scope": "local",
        "home_location": {
            "provider": "google",
            "provider_location_id": "lahore-id",
            "canonical_name": "Lahore, Pakistan",
            "country_code": "pk",
            "latitude": 31.5204,
            "longitude": 74.3587,
        },
    }


def test_update_accepts_canonical_home_and_normalizes_country() -> None:
    """Flutter's selected location should retain provider identity."""

    request = UserPreferenceUpdateRequest.model_validate(valid_payload())

    assert request.home_location is not None
    assert request.home_location.country_code == "PK"


def test_update_rejects_duplicate_interests() -> None:
    """Duplicate normalized rows should fail before a database transaction."""

    payload = valid_payload()
    payload["interests"] = ["hiking", "hiking"]

    with pytest.raises(ValidationError, match="must not contain duplicates"):
        UserPreferenceUpdateRequest.model_validate(payload)


def test_update_rejects_duplicate_travel_styles() -> None:
    """Duplicate styles should fail before a database transaction."""

    payload = valid_payload()
    payload["travel_styles"] = ["nature", "nature"]

    with pytest.raises(ValidationError, match="must not contain duplicates"):
        UserPreferenceUpdateRequest.model_validate(payload)


@pytest.mark.parametrize("scope", ["local", "international"])
def test_geographic_scope_requires_home_location(scope: str) -> None:
    """Locality filters cannot be correct without an explicit home country."""

    payload = valid_payload()
    payload["recommendation_scope"] = scope
    payload["home_location"] = None

    with pytest.raises(ValidationError, match="home_location is required"):
        UserPreferenceUpdateRequest.model_validate(payload)


def test_both_scope_allows_missing_home_location() -> None:
    """A user may receive general mixed suggestions without sharing a city."""

    payload = valid_payload()
    payload["recommendation_scope"] = "both"
    payload["home_location"] = None

    request = UserPreferenceUpdateRequest.model_validate(payload)

    assert request.home_location is None
