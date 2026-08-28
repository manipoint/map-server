"""Tests for the trip persistence model."""

from sqlalchemy import CheckConstraint

from app.database.models import Trip
from app.domain.trips import TripStatus


def test_trip_uses_application_schema() -> None:
    """Trips should live in the application schema."""

    assert Trip.__table__.schema == "app"
    assert Trip.__table__.name == "trips"
    assert Trip.__table__.fullname == "app.trips"


def test_trip_contains_required_columns() -> None:
    """Trip records should contain ownership, route, dates, and lifecycle data."""

    assert set(Trip.__table__.columns.keys()) == {
        "id",
        "user_id",
        "title",
        "origin",
        "destination",
        "origin_location_provider",
        "origin_provider_location_id",
        "origin_canonical_name",
        "origin_country_code",
        "origin_latitude",
        "origin_longitude",
        "destination_location_provider",
        "destination_provider_location_id",
        "destination_canonical_name",
        "destination_country_code",
        "destination_latitude",
        "destination_longitude",
        "start_date",
        "end_date",
        "status",
        "created_at",
        "updated_at",
    }
    assert Trip.__table__.c.id.primary_key is True
    assert Trip.__table__.c.user_id.nullable is False
    assert Trip.__table__.c.title.nullable is True
    assert Trip.__table__.c.origin.nullable is True
    assert Trip.__table__.c.destination.nullable is False
    assert Trip.__table__.c.start_date.nullable is False
    assert Trip.__table__.c.end_date.nullable is False
    assert Trip.__table__.c.status.nullable is False


def test_trip_user_foreign_key_cascades_on_delete() -> None:
    """Deleting a user should remove trips owned by that user."""

    foreign_key = next(iter(Trip.__table__.c.user_id.foreign_keys))

    assert foreign_key.target_fullname == "app.users.id"
    assert foreign_key.ondelete == "CASCADE"


def test_trip_has_validation_constraints() -> None:
    """Database metadata should preserve trip validation rules."""

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in Trip.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(constraints) == {
        "ck_trips_date_order",
        "ck_trips_destination_length",
        "ck_trips_destination_latitude_range",
        "ck_trips_destination_location_complete",
        "ck_trips_destination_longitude_range",
        "ck_trips_origin_length",
        "ck_trips_origin_latitude_range",
        "ck_trips_origin_location_complete",
        "ck_trips_origin_longitude_range",
        "ck_trips_status",
        "ck_trips_title_length",
    }
    assert constraints["ck_trips_date_order"] == "end_date > start_date"
    assert all(status.value in constraints["ck_trips_status"] for status in TripStatus)


def test_trip_reconstructs_atomic_canonical_locations() -> None:
    """Flat persistence columns should expose one validated domain object."""

    trip = Trip(
        origin_location_provider="google",
        origin_provider_location_id="lahore-id",
        origin_canonical_name="Lahore, Pakistan",
        origin_country_code="PK",
        origin_latitude=31.5204,
        origin_longitude=74.3587,
    )

    assert trip.origin_location is not None
    assert trip.origin_location.provider == "google"
    assert trip.origin_location.country_code == "PK"
    assert trip.destination_location is None


def test_trip_has_owner_listing_indexes() -> None:
    """Composite indexes should support authenticated trip-list queries."""

    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Trip.__table__.indexes
    }

    assert indexes == {
        "ix_trips_user_status_start_date": (
            "user_id",
            "status",
            "start_date",
        ),
        "ix_trips_user_updated_at": ("user_id", "updated_at"),
    }


def test_trip_defaults_to_draft() -> None:
    """Python and database inserts should share the draft status default."""

    status = Trip.__table__.c.status

    assert status.default is not None
    assert status.default.arg == TripStatus.DRAFT.value
    assert status.server_default is not None
    assert str(status.server_default.arg) == "'draft'"


def test_trip_timestamps_are_timezone_aware() -> None:
    """Created and updated timestamps should preserve timezone information."""

    created_at = Trip.__table__.c.created_at
    updated_at = Trip.__table__.c.updated_at

    assert created_at.type.timezone is True
    assert updated_at.type.timezone is True
    assert created_at.server_default is not None
    assert updated_at.server_default is not None
    assert updated_at.onupdate is not None
