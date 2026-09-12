"""Add indexes for complete catalogue seek pagination and country filtering.

Revision ID: b7e2f9a41063
Revises: d4f8a2c7e910
"""

import sqlalchemy as sa

from alembic import op

revision = "b7e2f9a41063"
down_revision = "d4f8a2c7e910"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for collection in ("popular", "featured"):
        op.create_index(
            f"ix_destinations_{collection}_seek",
            "destinations",
            [f"{collection}_rank", "editorial_rank", "slug"],
            schema="app",
            postgresql_where=sa.text(f"is_published AND {collection}_rank IS NOT NULL"),
        )
    op.create_index(
        "ix_destinations_country_published",
        "destinations",
        ["country_code", "editorial_rank", "slug"],
        schema="app",
        postgresql_where=sa.text("is_published"),
    )


def downgrade() -> None:
    for name in (
        "ix_destinations_country_published",
        "ix_destinations_featured_seek",
        "ix_destinations_popular_seek",
    ):
        op.drop_index(name, table_name="destinations", schema="app")
