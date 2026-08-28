"""Persist versioned itineraries belonging to trips."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.domain.itineraries import ItineraryStatus


class Itinerary(Base):
    """One versioned itinerary belonging to a user-owned trip."""

    __tablename__ = "itineraries"
    __table_args__ = (
        CheckConstraint(
            "version >= 1",
            name="version_positive",
        ),
        CheckConstraint(
            "status IN ('draft', 'saved', 'superseded')",
            name="status",
        ),
        UniqueConstraint(
            "trip_id",
            "version",
            name="uq_itineraries_trip_version",
        ),
        UniqueConstraint(
            "source_message_id",
            name="uq_itineraries_source_message",
        ),
        Index(
            "ix_itineraries_trip_created_at",
            "trip_id",
            "created_at",
        ),
        Index(
            "uq_itineraries_one_saved_per_trip",
            "trip_id",
            unique=True,
            postgresql_where=text("status = 'saved'"),
        ),
        {"schema": "app"},
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    trip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "app.trips.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "app.messages.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ItineraryStatus.DRAFT.value,
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
