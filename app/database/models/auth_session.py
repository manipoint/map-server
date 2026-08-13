"""Authentication session persistence model."""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
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
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AuthSession(Base):
    """Rotating refresh-token session for one user device."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="expiration_after_creation",
        ),
        CheckConstraint(
            "rotated_at IS NULL OR rotated_at >= created_at",
            name="rotation_after_creation",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="revocation_after_creation",
        ),
        CheckConstraint(
            "replaced_by_session_id IS NULL OR rotated_at IS NOT NULL",
            name="replacement_requires_rotation",
        ),
        CheckConstraint(
            "replaced_by_session_id IS NULL OR replaced_by_session_id <> id",
            name="cannot_replace_itself",
        ),
        CheckConstraint(
            "revoke_reason IS NULL OR revoked_at IS NOT NULL",
            name="reason_requires_revocation",
        ),
        Index(
            "ix_auth_sessions_user_revoked",
            "user_id",
            "revoked_at",
        ),
        Index(
            "ix_auth_sessions_token_family",
            "token_family_id",
        ),
        Index(
            "ix_auth_sessions_expires_at",
            "expires_at",
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
        ForeignKey(
            "app.users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
    )
    token_family_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    device_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    device_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoke_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    replaced_by_session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "app.auth_sessions.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    ip_address: Mapped[IPv4Address | IPv6Address | None] = mapped_column(
        INET(),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
