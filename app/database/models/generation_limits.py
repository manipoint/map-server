"""Cross-worker generation admission and daily request accounting."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class GenerationLease(Base):
    __tablename__ = "generation_leases"
    __table_args__ = (
        Index("ix_generation_leases_expires_at", "expires_at"),
        UniqueConstraint(
            "user_id",
            "message_id",
            name="uq_generation_leases_user_message",
        ),
        {"schema": "app"},
    )

    token: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.users.id", ondelete="CASCADE"),
        index=True,
    )
    message_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GenerationUsage(Base):
    __tablename__ = "generation_usage"
    __table_args__ = (
        Index("ix_generation_usage_day", "day"),
        {"schema": "app"},
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[date] = mapped_column(Date(), primary_key=True)
    requests: Mapped[int] = mapped_column(Integer(), nullable=False)
