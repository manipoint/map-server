"""Tests for the travel conversation message persistence model."""

from sqlalchemy import CheckConstraint, Text, UniqueConstraint

from app.database.models import Message


def test_message_uses_application_schema() -> None:
    """Travel messages should live in the application schema."""

    assert Message.__table__.schema == "app"
    assert Message.__table__.name == "messages"
    assert Message.__table__.fullname == "app.messages"


def test_message_contains_required_columns() -> None:
    """Message metadata should include conversation and correlation fields."""

    assert set(Message.__table__.columns.keys()) == {
        "id",
        "conversation_id",
        "client_message_id",
        "reply_to_message_id",
        "role",
        "content",
        "created_at",
    }
    assert Message.__table__.c.id.primary_key is True
    assert Message.__table__.c.conversation_id.nullable is False
    assert Message.__table__.c.client_message_id.nullable is True
    assert Message.__table__.c.reply_to_message_id.nullable is True
    assert Message.__table__.c.role.nullable is False
    assert Message.__table__.c.content.nullable is False


def test_message_foreign_keys_define_delete_behavior() -> None:
    """Conversation and reply deletion should remove dependent messages."""

    conversation_foreign_key = next(
        iter(Message.__table__.c.conversation_id.foreign_keys)
    )
    reply_foreign_key = next(iter(Message.__table__.c.reply_to_message_id.foreign_keys))

    assert conversation_foreign_key.target_fullname == "app.conversations.id"
    assert conversation_foreign_key.ondelete == "CASCADE"
    assert reply_foreign_key.target_fullname == "app.messages.id"
    assert reply_foreign_key.ondelete == "CASCADE"
    assert Message.__table__.c.client_message_id.foreign_keys == set()


def test_client_message_id_is_unique() -> None:
    """A client retry should identify an existing user message."""

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in Message.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("client_message_id",) in unique_columns


def test_message_has_role_and_content_constraints() -> None:
    """Database metadata should enforce valid message shapes and content."""

    constraint_names = {
        constraint.name
        for constraint in Message.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert constraint_names == {
        "ck_messages_content_not_blank",
        "ck_messages_role",
        "ck_messages_role_identifiers",
    }


def test_message_has_conversation_history_index() -> None:
    """A composite index should support chronological history queries."""

    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Message.__table__.indexes
    }

    assert indexes == {
        "ix_messages_conversation_created_at": (
            "conversation_id",
            "created_at",
        )
    }


def test_message_content_supports_unbounded_text() -> None:
    """Assistant itineraries should not be limited to a short varchar."""

    assert isinstance(Message.__table__.c.content.type, Text)
    assert Message.__table__.c.role.type.length == 16


def test_message_created_at_is_timezone_aware() -> None:
    """Message ordering timestamps should preserve timezone information."""

    created_at = Message.__table__.c.created_at

    assert created_at.type.timezone is True
    assert created_at.server_default is not None


def test_reply_to_message_id_is_unique() -> None:
    """Only one assistant reply should be stored for each user message."""
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in Message.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("reply_to_message_id",) in unique_columns
