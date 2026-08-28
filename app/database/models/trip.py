"""Trip persistence model."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.domain.trips import CanonicalLocation, TripStatus


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
        CheckConstraint(
            "(origin_location_provider IS NULL AND "
            "origin_provider_location_id IS NULL AND "
            "origin_canonical_name IS NULL AND origin_country_code IS NULL AND "
            "origin_latitude IS NULL AND origin_longitude IS NULL) OR "
            "(origin_location_provider IS NOT NULL AND "
            "origin_provider_location_id IS NOT NULL AND "
            "origin_canonical_name IS NOT NULL AND "
            "origin_country_code IS NOT NULL AND origin_latitude IS NOT NULL AND "
            "origin_longitude IS NOT NULL)",
            name="origin_location_complete",
        ),
        CheckConstraint(
            "(destination_location_provider IS NULL AND "
            "destination_provider_location_id IS NULL AND "
            "destination_canonical_name IS NULL AND "
            "destination_country_code IS NULL AND "
            "destination_latitude IS NULL AND destination_longitude IS NULL) OR "
            "(destination_location_provider IS NOT NULL AND "
            "destination_provider_location_id IS NOT NULL AND "
            "destination_canonical_name IS NOT NULL AND "
            "destination_country_code IS NOT NULL AND "
            "destination_latitude IS NOT NULL AND "
            "destination_longitude IS NOT NULL)",
            name="destination_location_complete",
        ),
        CheckConstraint(
            "origin_latitude IS NULL OR origin_latitude BETWEEN -90 AND 90",
            name="origin_latitude_range",
        ),
        CheckConstraint(
            "origin_longitude IS NULL OR origin_longitude BETWEEN -180 AND 180",
            name="origin_longitude_range",
        ),
        CheckConstraint(
            "destination_latitude IS NULL OR destination_latitude BETWEEN -90 AND 90",
            name="destination_latitude_range",
        ),
        CheckConstraint(
            "destination_longitude IS NULL OR "
            "destination_longitude BETWEEN -180 AND 180",
            name="destination_longitude_range",
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
    origin_location_provider: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    origin_provider_location_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    origin_canonical_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    origin_country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    origin_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    destination_location_provider: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    destination_provider_location_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    destination_canonical_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    destination_country_code: Mapped[str | None] = mapped_column(
        String(2), nullable=True
    )
    destination_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    destination_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
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

    @property
    def origin_location(self) -> CanonicalLocation | None:
        """Return atomic origin metadata when the endpoint is resolved."""

        return self._canonical_location("origin")

    @property
    def destination_location(self) -> CanonicalLocation | None:
        """Return atomic destination metadata when the endpoint is resolved."""

        return self._canonical_location("destination")

    def _canonical_location(self, prefix: str) -> CanonicalLocation | None:
        """Reconstruct one validated location from its persistence columns."""

        provider = getattr(self, f"{prefix}_location_provider")
        if provider is None:
            return None
        return CanonicalLocation(
            provider=provider,
            provider_location_id=getattr(self, f"{prefix}_provider_location_id"),
            canonical_name=getattr(self, f"{prefix}_canonical_name"),
            country_code=getattr(self, f"{prefix}_country_code"),
            latitude=getattr(self, f"{prefix}_latitude"),
            longitude=getattr(self, f"{prefix}_longitude"),
        )
