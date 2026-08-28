"""add trip context to messages

Revision ID: 4bd392a8d0c1
Revises: db5b0ae0df09
Create Date: 2026-08-28 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4bd392a8d0c1"
down_revision: str | Sequence[str] | None = "db5b0ae0df09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add optional durable trip context to user messages."""

    op.add_column(
        "messages",
        sa.Column("trip_id", sa.Uuid(), nullable=True),
        schema="app",
    )
    op.create_foreign_key(
        op.f("fk_messages_trip_id_trips"),
        "messages",
        "trips",
        ["trip_id"],
        ["id"],
        source_schema="app",
        referent_schema="app",
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_messages_trip_context_user_only"),
        "messages",
        "role = 'user' OR trip_id IS NULL",
        schema="app",
    )
    op.create_index(
        "ix_messages_trip_created_at",
        "messages",
        ["trip_id", "created_at"],
        unique=False,
        schema="app",
    )


def downgrade() -> None:
    """Remove durable trip context from messages."""

    op.drop_index(
        "ix_messages_trip_created_at",
        table_name="messages",
        schema="app",
    )
    op.drop_constraint(
        op.f("ck_messages_trip_context_user_only"),
        "messages",
        schema="app",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_messages_trip_id_trips"),
        "messages",
        schema="app",
        type_="foreignkey",
    )
    op.drop_column("messages", "trip_id", schema="app")
