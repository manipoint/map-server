"""Tests for normalized preference persistence metadata."""

from sqlalchemy import CheckConstraint

from app.database.models import UserInterest, UserPreference, UserTravelStyle


def test_user_preference_is_one_to_one_with_user() -> None:
    """The user foreign key should also be the preference primary key."""

    user_id = UserPreference.__table__.c.user_id
    foreign_key = next(iter(user_id.foreign_keys))

    assert UserPreference.__table__.schema == "app"
    assert user_id.primary_key is True
    assert foreign_key.target_fullname == "app.users.id"
    assert foreign_key.ondelete == "CASCADE"


def test_user_preference_has_atomic_home_location_constraints() -> None:
    """Partial provider locations must be rejected at the database boundary."""

    names = {
        constraint.name
        for constraint in UserPreference.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_user_preferences_home_location_complete" in names
    assert "ck_user_preferences_home_latitude_range" in names
    assert "ck_user_preferences_home_longitude_range" in names


def test_user_interest_uses_normalized_composite_primary_key() -> None:
    """Repeated interests should be impossible without JSON or arrays."""

    primary_key = tuple(
        column.name for column in UserInterest.__table__.primary_key.columns
    )
    foreign_key = next(iter(UserInterest.__table__.c.user_id.foreign_keys))

    assert UserInterest.__table__.schema == "app"
    assert primary_key == ("user_id", "interest")
    assert foreign_key.target_fullname == "app.user_preferences.user_id"
    assert foreign_key.ondelete == "CASCADE"


def test_user_travel_style_uses_normalized_composite_primary_key() -> None:
    """Users should select multiple styles without arrays or scalar duplication."""

    primary_key = tuple(
        column.name for column in UserTravelStyle.__table__.primary_key.columns
    )
    foreign_key = next(iter(UserTravelStyle.__table__.c.user_id.foreign_keys))

    assert UserTravelStyle.__table__.schema == "app"
    assert primary_key == ("user_id", "travel_style")
    assert foreign_key.target_fullname == "app.user_preferences.user_id"
    assert foreign_key.ondelete == "CASCADE"


def test_user_preference_reconstructs_canonical_home_location() -> None:
    """Flat location columns should expose one validated domain object."""

    preference = UserPreference(
        home_location_provider="google",
        home_provider_location_id="lahore-id",
        home_canonical_name="Lahore, Pakistan",
        home_country_code="PK",
        home_latitude=31.5204,
        home_longitude=74.3587,
    )

    assert preference.home_location is not None
    assert preference.home_location.country_code == "PK"
    assert preference.home_location.canonical_name == "Lahore, Pakistan"
