"""Tests for the itinerary-item persistence model."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.database.models import ItineraryItem
from app.domain.itineraries import ItineraryItemType


def test_itinerary_item_uses_application_schema() -> None:
    """Itinerary items should live in the application schema."""

    assert ItineraryItem.__table__.schema == "app"
    assert ItineraryItem.__table__.name == "itinerary_items"
    assert ItineraryItem.__table__.fullname == "app.itinerary_items"


def test_itinerary_item_contains_required_timeline_columns() -> None:
    """Items should retain ordering, display, location, and time data."""

    assert set(ItineraryItem.__table__.columns.keys()) == {
        "id",
        "itinerary_id",
        "day_number",
        "position",
        "item_type",
        "title",
        "description",
        "location_name",
        "starts_at",
        "ends_at",
        "created_at",
        "updated_at",
    }
    assert ItineraryItem.__table__.c.id.primary_key is True
    assert ItineraryItem.__table__.c.itinerary_id.nullable is False
    assert ItineraryItem.__table__.c.day_number.nullable is False
    assert ItineraryItem.__table__.c.position.nullable is False
    assert ItineraryItem.__table__.c.item_type.nullable is False
    assert ItineraryItem.__table__.c.title.nullable is False
    assert ItineraryItem.__table__.c.description.nullable is True
    assert ItineraryItem.__table__.c.location_name.nullable is True
    assert ItineraryItem.__table__.c.starts_at.nullable is True
    assert ItineraryItem.__table__.c.ends_at.nullable is True


def test_itinerary_item_foreign_key_cascades_on_delete() -> None:
    """Deleting an itinerary version should remove all of its items."""

    foreign_key = next(iter(ItineraryItem.__table__.c.itinerary_id.foreign_keys))

    assert foreign_key.target_fullname == "app.itineraries.id"
    assert foreign_key.ondelete == "CASCADE"


def test_itinerary_item_has_validation_constraints() -> None:
    """Database metadata should protect ordering, types, titles, and times."""

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in ItineraryItem.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert constraints["ck_itinerary_items_day_number_positive"] == ("day_number >= 1")
    assert constraints["ck_itinerary_items_position_positive"] == "position >= 1"
    assert constraints["ck_itinerary_items_title_length"] == (
        "char_length(btrim(title)) BETWEEN 1 AND 200"
    )
    assert constraints["ck_itinerary_items_time_order"] == (
        "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at"
    )
    assert all(
        item_type.value in constraints["ck_itinerary_items_item_type"]
        for item_type in ItineraryItemType
    )


def test_itinerary_item_positions_are_unique_within_each_day() -> None:
    """One itinerary day must not contain two items at one position."""

    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in ItineraryItem.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_constraints["uq_itinerary_items_itinerary_day_position"] == (
        "itinerary_id",
        "day_number",
        "position",
    )


def test_itinerary_item_times_and_timestamps_are_timezone_aware() -> None:
    """Timeline and audit timestamps should preserve timezone information."""

    assert ItineraryItem.__table__.c.starts_at.type.timezone is True
    assert ItineraryItem.__table__.c.ends_at.type.timezone is True

    created_at = ItineraryItem.__table__.c.created_at
    updated_at = ItineraryItem.__table__.c.updated_at
    assert created_at.type.timezone is True
    assert updated_at.type.timezone is True
    assert created_at.server_default is not None
    assert updated_at.server_default is not None
    assert updated_at.onupdate is not None
