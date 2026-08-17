"""Assistant response processing lease model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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
from app.domain.enums import AssistantRunStatus


class AssistantRun(Base):
    """Coordinate exactly one assistant response for a user message."""

    __tablename__ = "assistant_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="status",
        ),
        CheckConstraint(
            "attempt_count >= 1",
            name="attempt_count_positive",
        ),
        CheckConstraint(
            "lease_expires_at IS NULL OR lease_expires_at > created_at",
            name="lease_after_creation",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR "
            "char_length(btrim(last_error_code)) BETWEEN 1 AND 64",
            name="error_code_length",
        ),
        CheckConstraint(
            "("
            "status = 'processing' "
            "AND claim_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND assistant_message_id IS NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'completed' "
            "AND claim_token IS NULL "
            "AND lease_expires_at IS NULL "
            "AND assistant_message_id IS NOT NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'failed' "
            "AND claim_token IS NULL "
            "AND lease_expires_at IS NULL "
            "AND assistant_message_id IS NULL "
            "AND last_error_code IS NOT NULL"
            ")",
            name="state_shape",
        ),
        Index(
            "ix_assistant_runs_status_lease",
            "status",
            "lease_expires_at",
        ),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    assistant_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.messages.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=AssistantRunStatus.PROCESSING.value,
        server_default=text("'processing'"),
    )
    claim_token: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(64),
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
