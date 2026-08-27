"""Trip persistence model."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
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
from app.domain.trips import TripStatus


class Trip(Base):
    """One travel plan owned by a registered user."""

    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint(
            "title IS NULL OR char_length(btrim(title)) BETWEEN 1 AND 160",
            name="title_length",
        ),
        CheckConstraint(
            "origin IS NULL OR char_length(btrim(origin)) BETWEEN 2 AND 120",
            name="origin_length",
        ),
        CheckConstraint(
            "char_length(btrim(destination)) BETWEEN 2 AND 120",
            name="destination_length",
        ),
        CheckConstraint(
            "end_date > start_date",
            name="date_order",
        ),
        CheckConstraint(
            "status IN ('draft', 'planned', 'archived')",
            name="status",
        ),
        Index(
            "ix_trips_user_updated_at",
            "user_id",
            "updated_at",
        ),
        Index(
            "ix_trips_user_status_start_date",
            "user_id",
            "status",
            "start_date",
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
    origin: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    destination: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TripStatus.DRAFT.value,
        server_default=text("'draft'"),
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
