"""Tests for the travel conversation persistence model."""

from sqlalchemy import CheckConstraint

from app.database.models import Conversation


def test_conversation_uses_application_schema() -> None:
    """Travel conversations should live in the application schema."""

    assert Conversation.__table__.schema == "app"
    assert Conversation.__table__.name == "conversations"
    assert Conversation.__table__.fullname == "app.conversations"


def test_conversation_contains_required_columns() -> None:
    """Conversation metadata should include ownership and display fields."""

    assert set(Conversation.__table__.columns.keys()) == {
        "id",
        "user_id",
        "title",
        "locale",
        "created_at",
        "updated_at",
    }
    assert Conversation.__table__.c.id.primary_key is True
    assert Conversation.__table__.c.user_id.nullable is False
    assert Conversation.__table__.c.title.nullable is True
    assert Conversation.__table__.c.locale.nullable is False


def test_conversation_user_foreign_key_cascades_on_delete() -> None:
    """Deleting a user should remove conversations that it owns."""

    foreign_key = next(iter(Conversation.__table__.c.user_id.foreign_keys))

    assert foreign_key.target_fullname == "app.users.id"
    assert foreign_key.ondelete == "CASCADE"


def test_conversation_has_content_constraints() -> None:
    """Database metadata should reject invalid titles and locales."""

    constraint_names = {
        constraint.name
        for constraint in Conversation.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert constraint_names == {
        "ck_conversations_locale_length",
        "ck_conversations_title_length",
    }


def test_conversation_has_recent_user_lookup_index() -> None:
    """A composite index should support a user's recent conversation list."""

    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Conversation.__table__.indexes
    }

    assert indexes == {"ix_conversations_user_updated_at": ("user_id", "updated_at")}


def test_conversation_locale_defaults_to_english() -> None:
    """Python and database inserts should share the same locale default."""

    locale = Conversation.__table__.c.locale

    assert locale.default is not None
    assert locale.default.arg == "en"
    assert locale.server_default is not None
    assert str(locale.server_default.arg) == "'en'"


def test_conversation_timestamps_are_timezone_aware() -> None:
    """Created and updated timestamps should preserve timezone information."""

    created_at = Conversation.__table__.c.created_at
    updated_at = Conversation.__table__.c.updated_at

    assert created_at.type.timezone is True
    assert updated_at.type.timezone is True
    assert created_at.server_default is not None
    assert updated_at.server_default is not None
    assert updated_at.onupdate is not None
