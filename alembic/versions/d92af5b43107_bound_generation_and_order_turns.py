"""Bound cross-worker generation and order conversation turns.

Revision ID: d92af5b43107
Revises: c8e3a9f21064
"""

import sqlalchemy as sa

from alembic import op

revision = "d92af5b43107"
down_revision = "c8e3a9f21064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "generation_deferred",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="app",
    )
    for name in ("start_time_zone", "end_time_zone"):
        op.add_column(
            "itinerary_items",
            sa.Column(name, sa.String(64), nullable=True),
            schema="app",
        )
    # This backfill holds the table lock. Schedule a maintenance window for a
    # large messages table; no application turn may be inserted during ranking.
    op.add_column(
        "messages",
        sa.Column("turn_number", sa.BigInteger(), sa.Identity(), nullable=False),
        schema="app",
    )
    op.execute(
        "WITH ordered AS (SELECT id, row_number() OVER (ORDER BY created_at, id) AS n FROM app.messages) UPDATE app.messages SET turn_number = ordered.n FROM ordered WHERE messages.id = ordered.id"
    )
    op.execute(
        "SELECT setval(pg_get_serial_sequence('app.messages', 'turn_number'), coalesce((SELECT max(turn_number) FROM app.messages), 0) + 1, false)"
    )
    op.create_index(
        "ix_messages_conversation_turn",
        "messages",
        ["conversation_id", "turn_number"],
        schema="app",
    )
    op.create_table(
        "generation_leases",
        sa.Column("token", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_generation_leases_expires_at",
        "generation_leases",
        ["expires_at"],
        schema="app",
    )
    op.create_index(
        "ix_app_generation_leases_user_id",
        "generation_leases",
        ["user_id"],
        schema="app",
    )
    op.create_table(
        "generation_usage",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("requests", sa.Integer(), nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_generation_usage_day", "generation_usage", ["day"], schema="app"
    )


def downgrade() -> None:
    op.drop_column("messages", "generation_deferred", schema="app")
    for name in ("start_time_zone", "end_time_zone"):
        op.drop_column("itinerary_items", name, schema="app")
    # Discards admission accounting and turn order, but retains conversation data.
    op.drop_table("generation_usage", schema="app")
    op.drop_table("generation_leases", schema="app")
    op.drop_index("ix_messages_conversation_turn", table_name="messages", schema="app")
    op.drop_column("messages", "turn_number", schema="app")
