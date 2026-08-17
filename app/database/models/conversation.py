"""Travel conversation persistence model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Conversation(Base):
    """One travel-assistant conversation owned by a user."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "title IS NULL OR char_length(btrim(title)) BETWEEN 1 AND 160",
            name="title_length",
        ),
        CheckConstraint(
            "char_length(btrim(locale)) BETWEEN 2 AND 35",
            name="locale_length",
        ),
        Index(
            "ix_conversations_user_updated_at",
            "user_id",
            "updated_at",
        ),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )
    locale: Mapped[str] = mapped_column(
        String(35),
        nullable=False,
        default="en",
        server_default=text("'en'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
