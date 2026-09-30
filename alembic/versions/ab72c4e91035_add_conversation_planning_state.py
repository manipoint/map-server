"""Persist conversational planning and cross-worker leases.

Revision ID: ab72c4e91035
Revises: f3a9c2d7e641
Downgrade discards collected requirements and sticky trip associations.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ab72c4e91035"
down_revision = "f3a9c2d7e641"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "planning_state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="app",
    )
    op.add_column(
        "conversations",
        sa.Column("planning_trip_id", sa.Uuid(), nullable=True),
        schema="app",
    )
    op.add_column(
        "conversations",
        sa.Column("planning_lease_token", sa.Uuid(), nullable=True),
        schema="app",
    )
    op.add_column(
        "conversations",
        sa.Column(
            "planning_lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        schema="app",
    )
    op.create_foreign_key(
        "fk_conversations_planning_trip_id_trips",
        "conversations",
        "trips",
        ["planning_trip_id"],
        ["id"],
        source_schema="app",
        referent_schema="app",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_conversations_planning_trip_id_trips",
        "conversations",
        type_="foreignkey",
        schema="app",
    )
    for column in (
        "planning_lease_expires_at",
        "planning_lease_token",
        "planning_trip_id",
        "planning_state",
    ):
        op.drop_column("conversations", column, schema="app")
