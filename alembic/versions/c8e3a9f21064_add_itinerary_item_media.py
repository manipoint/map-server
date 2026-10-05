"""Persist verified itinerary activity images.

Revision ID: c8e3a9f21064
Revises: ab72c4e91035
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c8e3a9f21064"
down_revision = "ab72c4e91035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "itinerary_items",
        sa.Column("image", postgresql.JSONB(), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    # Downgrading discards activity image metadata, retaining the schedule.
    op.drop_column("itinerary_items", "image", schema="app")
