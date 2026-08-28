"""Persist ordered timeline items belonging to itineraries."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.domain.itineraries import ItineraryItemType

ITINERARY_ITEM_TYPE_SQL_VALUES = ", ".join(
    f"'{item_type.value}'" for item_type in ItineraryItemType
)


class ItineraryItem(Base):
    """One ordered activity in an itinerary day."""

    __tablename__ = "itinerary_items"
    __table_args__ = (
        CheckConstraint(
            "day_number >= 1",
            name="day_number_positive",
        ),
        CheckConstraint(
            "position >= 1",
            name="position_positive",
        ),
        CheckConstraint(
            f"item_type IN ({ITINERARY_ITEM_TYPE_SQL_VALUES})",
            name="item_type",
        ),
        CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200",
            name="title_length",
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="time_order",
        ),
        UniqueConstraint(
            "itinerary_id",
            "day_number",
            "position",
            name="uq_itinerary_items_itinerary_day_position",
        ),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    itinerary_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "app.itineraries.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    day_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    item_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    location_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
