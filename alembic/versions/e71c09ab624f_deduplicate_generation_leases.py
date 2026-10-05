"""Add message-scoped generation lease deduplication.

Revision ID: e71c09ab624f
Revises: d92af5b43107
"""

import sqlalchemy as sa

from alembic import op

revision = "e71c09ab624f"
down_revision = "d92af5b43107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_leases",
        sa.Column("message_id", sa.Uuid(), nullable=True),
        schema="app",
    )
    op.create_unique_constraint(
        "uq_generation_leases_user_message",
        "generation_leases",
        ["user_id", "message_id"],
        schema="app",
    )


def downgrade() -> None:
    # Discards lease deduplication metadata, not conversation messages.
    op.drop_constraint(
        "uq_generation_leases_user_message",
        "generation_leases",
        type_="unique",
        schema="app",
    )
    op.drop_column("generation_leases", "message_id", schema="app")
