"""Normalized user travel-preference persistence models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
)
from app.domain.trips import CanonicalLocation


def _allowed_values(values: type[StrEnum]) -> str:
    """Return SQL-safe quoted values for static check constraints."""

    return ", ".join(f"'{value.value}'" for value in values)


class UserPreference(Base):
    """One user's scalar onboarding choices and canonical home location."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint(
            f"budget_tier IS NULL OR budget_tier IN ({_allowed_values(BudgetTier)})",
            name="budget_tier",
        ),
        CheckConstraint(
            f"trip_pace IS NULL OR trip_pace IN ({_allowed_values(TripPace)})",
            name="trip_pace",
        ),
        CheckConstraint(
            f"recommendation_scope IN ({_allowed_values(RecommendationScope)})",
            name="recommendation_scope",
        ),
        CheckConstraint(
            "(home_location_provider IS NULL AND "
            "home_provider_location_id IS NULL AND "
            "home_canonical_name IS NULL AND home_country_code IS NULL AND "
            "home_latitude IS NULL AND home_longitude IS NULL) OR "
            "(home_location_provider IS NOT NULL AND "
            "home_provider_location_id IS NOT NULL AND "
            "home_canonical_name IS NOT NULL AND home_country_code IS NOT NULL AND "
            "home_latitude IS NOT NULL AND home_longitude IS NOT NULL)",
            name="home_location_complete",
        ),
        CheckConstraint(
            "home_latitude IS NULL OR home_latitude BETWEEN -90 AND 90",
            name="home_latitude_range",
        ),
        CheckConstraint(
            "home_longitude IS NULL OR home_longitude BETWEEN -180 AND 180",
            name="home_longitude_range",
        ),
        {"schema": "app"},
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    budget_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trip_pace: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recommendation_scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=RecommendationScope.BOTH.value,
        server_default=text("'both'"),
    )
    home_location_provider: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    home_provider_location_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    home_canonical_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    home_country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    home_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def home_location(self) -> CanonicalLocation | None:
        """Reconstruct the atomic canonical home location."""

        if self.home_location_provider is None:
            return None
        return CanonicalLocation(
            provider=self.home_location_provider,
            provider_location_id=self.home_provider_location_id,
            canonical_name=self.home_canonical_name,
            country_code=self.home_country_code,
            latitude=self.home_latitude,
            longitude=self.home_longitude,
        )


class UserInterest(Base):
    """One normalized interest selected by one user."""

    __tablename__ = "user_interests"
    __table_args__ = (
        CheckConstraint(
            f"interest IN ({_allowed_values(TravelInterest)})",
            name="interest",
        ),
        {"schema": "app"},
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.user_preferences.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    interest: Mapped[str] = mapped_column(String(32), primary_key=True)


class UserTravelStyle(Base):
    """One normalized travel style selected by one user."""

    __tablename__ = "user_travel_styles"
    __table_args__ = (
        CheckConstraint(
            f"travel_style IN ({_allowed_values(TravelStyle)})",
            name="travel_style",
        ),
        {"schema": "app"},
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.user_preferences.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    travel_style: Mapped[str] = mapped_column(String(32), primary_key=True)
