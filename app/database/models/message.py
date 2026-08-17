"""Travel conversation message persistence model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Message(Base):
    """One user or assistant message in a travel conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="role",
        ),
        CheckConstraint(
            "char_length(btrim(content)) > 0",
            name="content_not_blank",
        ),
        CheckConstraint(
            "(role = 'user' AND client_message_id IS NOT NULL "
            "AND reply_to_message_id IS NULL) OR "
            "(role = 'assistant' AND client_message_id IS NULL "
            "AND reply_to_message_id IS NOT NULL)",
            name="role_identifiers",
        ),
        Index(
            "ix_messages_conversation_created_at",
            "conversation_id",
            "created_at",
        ),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        unique=True,
    )
    reply_to_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.messages.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
