"""Curated destination catalogue persistence models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle


def _allowed_values(values: type[StrEnum]) -> str:
    """Return quoted enum values for static database constraints."""

    return ", ".join(f"'{value.value}'" for value in values)


class Destination(Base):
    """One editorially managed and publicly discoverable destination."""

    __tablename__ = "destinations"
    __table_args__ = (
        CheckConstraint(
            "char_length(slug) BETWEEN 2 AND 120 AND slug = lower(slug)",
            name="slug_format",
        ),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 2 AND 120",
            name="name_length",
        ),
        CheckConstraint(
            "char_length(btrim(country_name)) BETWEEN 2 AND 120",
            name="country_name_length",
        ),
        CheckConstraint(
            "char_length(country_code) = 2 AND country_code = upper(country_code)",
            name="country_code_format",
        ),
        CheckConstraint(
            "char_length(btrim(summary)) BETWEEN 20 AND 600",
            name="summary_length",
        ),
        CheckConstraint(
            f"budget_tier IN ({_allowed_values(BudgetTier)})",
            name="budget_tier",
        ),
        CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name="latitude_range",
        ),
        CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name="longitude_range",
        ),
        CheckConstraint("editorial_rank > 0", name="editorial_rank_positive"),
        CheckConstraint(
            "(is_featured AND featured_rank IS NOT NULL AND featured_rank > 0) "
            "OR (NOT is_featured AND featured_rank IS NULL)",
            name="featured_rank_consistent",
        ),
        CheckConstraint(
            "(is_popular AND popular_rank IS NOT NULL AND popular_rank > 0) "
            "OR (NOT is_popular AND popular_rank IS NULL)",
            name="popular_rank_consistent",
        ),
        Index(
            "ix_destinations_published_editorial",
            "is_published",
            "editorial_rank",
        ),
        Index(
            "ix_destinations_published_featured",
            "is_published",
            "is_featured",
            "featured_rank",
        ),
        Index(
            "ix_destinations_published_popular",
            "is_published",
            "is_popular",
            "popular_rank",
        ),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    country_name: Mapped[str] = mapped_column(String(120), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    summary: Mapped[str] = mapped_column(String(600), nullable=False)
    image_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    image_alt: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    budget_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    is_published: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    is_featured: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    featured_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_popular: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    popular_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    editorial_rank: Mapped[int] = mapped_column(Integer, nullable=False)
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


class DestinationStyle(Base):
    """One normalized travel style supported by a destination."""

    __tablename__ = "destination_styles"
    __table_args__ = (
        CheckConstraint(
            f"style IN ({_allowed_values(TravelStyle)})",
            name="style",
        ),
        {"schema": "app"},
    )

    destination_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.destinations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    style: Mapped[str] = mapped_column(String(32), primary_key=True)


class DestinationInterest(Base):
    """One normalized interest supported by a destination."""

    __tablename__ = "destination_interests"
    __table_args__ = (
        CheckConstraint(
            f"interest IN ({_allowed_values(TravelInterest)})",
            name="interest",
        ),
        {"schema": "app"},
    )

    destination_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.destinations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    interest: Mapped[str] = mapped_column(String(32), primary_key=True)
