"""Tests for the itinerary persistence model."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.database.models import Itinerary
from app.domain.itineraries import ItineraryStatus


def test_itinerary_uses_application_schema() -> None:
    """Itineraries should live in the application schema."""

    assert Itinerary.__table__.schema == "app"
    assert Itinerary.__table__.name == "itineraries"
    assert Itinerary.__table__.fullname == "app.itineraries"


def test_itinerary_contains_required_columns() -> None:
    """Each version should store its trip, lifecycle, and timestamps."""

    assert set(Itinerary.__table__.columns.keys()) == {
        "id",
        "trip_id",
        "source_message_id",
        "version",
        "status",
        "created_at",
        "updated_at",
    }
    assert Itinerary.__table__.c.id.primary_key is True
    assert Itinerary.__table__.c.trip_id.nullable is False
    assert Itinerary.__table__.c.version.nullable is False
    assert Itinerary.__table__.c.status.nullable is False


def test_itinerary_trip_foreign_key_cascades_on_delete() -> None:
    """Permanent trip deletion should remove all itinerary versions."""

    foreign_key = next(iter(Itinerary.__table__.c.trip_id.foreign_keys))

    assert foreign_key.target_fullname == "app.trips.id"
    assert foreign_key.ondelete == "CASCADE"


def test_itinerary_has_version_and_status_constraints() -> None:
    """Database metadata should protect version and lifecycle values."""

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in Itinerary.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert constraints["ck_itineraries_version_positive"] == "version >= 1"
    assert all(
        status.value in constraints["ck_itineraries_status"]
        for status in ItineraryStatus
    )


def test_itinerary_versions_are_unique_within_trip() -> None:
    """A trip must not contain duplicate itinerary version numbers."""

    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in Itinerary.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_constraints["uq_itineraries_trip_version"] == (
        "trip_id",
        "version",
    )


def test_itinerary_indexes_support_history_and_single_saved_version() -> None:
    """Indexes should support history reads and one saved version per trip."""

    indexes = {index.name: index for index in Itinerary.__table__.indexes}

    history_index = indexes["ix_itineraries_trip_created_at"]
    assert tuple(column.name for column in history_index.columns) == (
        "trip_id",
        "created_at",
    )
    assert history_index.unique is False

    saved_index = indexes["uq_itineraries_one_saved_per_trip"]
    assert tuple(column.name for column in saved_index.columns) == ("trip_id",)
    assert saved_index.unique is True
    assert str(saved_index.dialect_options["postgresql"]["where"]) == (
        "status = 'saved'"
    )


def test_itinerary_defaults_to_draft() -> None:
    """Python and database inserts should share the draft default."""

    status = Itinerary.__table__.c.status

    assert status.default is not None
    assert status.default.arg == ItineraryStatus.DRAFT.value
    assert status.server_default is not None
    assert str(status.server_default.arg) == "'draft'"


def test_itinerary_timestamps_are_timezone_aware() -> None:
    """Created and updated timestamps should retain timezone information."""

    created_at = Itinerary.__table__.c.created_at
    updated_at = Itinerary.__table__.c.updated_at

    assert created_at.type.timezone is True
    assert updated_at.type.timezone is True
    assert created_at.server_default is not None
    assert updated_at.server_default is not None
    assert updated_at.onupdate is not None


def test_itinerary_has_optional_unique_source_message() -> None:
    """Generated drafts should be idempotent per originating message."""

    column = Itinerary.__table__.c.source_message_id

    assert column.nullable is True

    foreign_key = next(iter(column.foreign_keys))
    assert foreign_key.target_fullname == "app.messages.id"
    assert foreign_key.ondelete == "SET NULL"

    unique_constraints = {
        constraint.name
        for constraint in Itinerary.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_itineraries_source_message" in unique_constraints
