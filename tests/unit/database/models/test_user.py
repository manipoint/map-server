"""Tests for the user persistence model."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.database.models import User


def test_user_table_uses_application_schema() -> None:
    """User data should live in the application schema."""
    assert User.__table__.schema == "app"
    assert User.__table__.name == "users"
    assert User.__table__.fullname == "app.users"


def test_user_table_contains_required_columns() -> None:
    """User table should expose its required persistence fields."""

    assert set(User.__table__.columns.keys()) == {
        "id",
        "email",
        "password_hash",
        "status",
        "created_at",
        "updated_at",
    }
    assert User.__table__.c.id.primary_key is True
    assert User.__table__.c.email.nullable is False
    assert User.__table__.c.password_hash.nullable is False


def test_user_email_is_unique() -> None:
    """Database metadata should enforce one account per email."""
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in User.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("email",) in unique_columns


def test_user_table_has_validation_constraints() -> None:
    """Database should validate normalized emails and user status."""
    constraint_names = {
        constraint.name
        for constraint in User.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_users_email_normalized" in constraint_names
    assert "ck_users_status" in constraint_names
