"""add canonical trip locations

Revision ID: 91d3f4a6c8b2
Revises: fbc765d13a3b
Create Date: 2026-08-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91d3f4a6c8b2"
down_revision: str | Sequence[str] | None = "fbc765d13a3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LOCATION_PREFIXES = ("origin", "destination")


def upgrade() -> None:
    """Add optional, provider-qualified route endpoint metadata."""

    for prefix in LOCATION_PREFIXES:
        op.add_column(
            "trips",
            sa.Column(f"{prefix}_location_provider", sa.String(32), nullable=True),
            schema="app",
        )
        op.add_column(
            "trips",
            sa.Column(f"{prefix}_provider_location_id", sa.String(256), nullable=True),
            schema="app",
        )
        op.add_column(
            "trips",
            sa.Column(f"{prefix}_canonical_name", sa.String(200), nullable=True),
            schema="app",
        )
        op.add_column(
            "trips",
            sa.Column(f"{prefix}_country_code", sa.String(2), nullable=True),
            schema="app",
        )
        op.add_column(
            "trips",
            sa.Column(f"{prefix}_latitude", sa.Float(), nullable=True),
            schema="app",
        )
        op.add_column(
            "trips",
            sa.Column(f"{prefix}_longitude", sa.Float(), nullable=True),
            schema="app",
        )

        op.create_check_constraint(
            f"ck_trips_{prefix}_location_complete",
            "trips",
            (
                f"({prefix}_location_provider IS NULL AND "
                f"{prefix}_provider_location_id IS NULL AND "
                f"{prefix}_canonical_name IS NULL AND "
                f"{prefix}_country_code IS NULL AND "
                f"{prefix}_latitude IS NULL AND {prefix}_longitude IS NULL) OR "
                f"({prefix}_location_provider IS NOT NULL AND "
                f"{prefix}_provider_location_id IS NOT NULL AND "
                f"{prefix}_canonical_name IS NOT NULL AND "
                f"{prefix}_country_code IS NOT NULL AND "
                f"{prefix}_latitude IS NOT NULL AND "
                f"{prefix}_longitude IS NOT NULL)"
            ),
            schema="app",
        )
        op.create_check_constraint(
            f"ck_trips_{prefix}_latitude_range",
            "trips",
            f"{prefix}_latitude IS NULL OR {prefix}_latitude BETWEEN -90 AND 90",
            schema="app",
        )
        op.create_check_constraint(
            f"ck_trips_{prefix}_longitude_range",
            "trips",
            (f"{prefix}_longitude IS NULL OR {prefix}_longitude BETWEEN -180 AND 180"),
            schema="app",
        )


def downgrade() -> None:
    """Remove canonical route endpoint metadata."""

    for prefix in reversed(LOCATION_PREFIXES):
        op.drop_constraint(
            f"ck_trips_{prefix}_longitude_range",
            "trips",
            schema="app",
            type_="check",
        )
        op.drop_constraint(
            f"ck_trips_{prefix}_latitude_range",
            "trips",
            schema="app",
            type_="check",
        )
        op.drop_constraint(
            f"ck_trips_{prefix}_location_complete",
            "trips",
            schema="app",
            type_="check",
        )
        for suffix in (
            "longitude",
            "latitude",
            "country_code",
            "canonical_name",
            "provider_location_id",
            "location_provider",
        ):
            op.drop_column("trips", f"{prefix}_{suffix}", schema="app")
