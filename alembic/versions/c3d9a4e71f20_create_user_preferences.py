"""create normalized user preferences

Revision ID: c3d9a4e71f20
Revises: a61c9e4b2d77
Create Date: 2026-09-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d9a4e71f20"
down_revision: str | Sequence[str] | None = "a61c9e4b2d77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one-to-one preferences and normalized interest selections."""

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("travel_style", sa.String(length=32), nullable=True),
        sa.Column("budget_tier", sa.String(length=32), nullable=True),
        sa.Column("trip_pace", sa.String(length=32), nullable=True),
        sa.Column(
            "recommendation_scope",
            sa.String(length=32),
            server_default=sa.text("'both'"),
            nullable=False,
        ),
        sa.Column("home_location_provider", sa.String(length=32), nullable=True),
        sa.Column("home_provider_location_id", sa.String(length=256), nullable=True),
        sa.Column("home_canonical_name", sa.String(length=200), nullable=True),
        sa.Column("home_country_code", sa.String(length=2), nullable=True),
        sa.Column("home_latitude", sa.Float(), nullable=True),
        sa.Column("home_longitude", sa.Float(), nullable=True),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "budget_tier IS NULL OR budget_tier IN "
            "('budget', 'mid_range', 'premium', 'luxury')",
            name=op.f("ck_user_preferences_budget_tier"),
        ),
        sa.CheckConstraint(
            "(home_location_provider IS NULL AND "
            "home_provider_location_id IS NULL AND "
            "home_canonical_name IS NULL AND home_country_code IS NULL AND "
            "home_latitude IS NULL AND home_longitude IS NULL) OR "
            "(home_location_provider IS NOT NULL AND "
            "home_provider_location_id IS NOT NULL AND "
            "home_canonical_name IS NOT NULL AND home_country_code IS NOT NULL AND "
            "home_latitude IS NOT NULL AND home_longitude IS NOT NULL)",
            name=op.f("ck_user_preferences_home_location_complete"),
        ),
        sa.CheckConstraint(
            "home_latitude IS NULL OR home_latitude BETWEEN -90 AND 90",
            name=op.f("ck_user_preferences_home_latitude_range"),
        ),
        sa.CheckConstraint(
            "home_longitude IS NULL OR home_longitude BETWEEN -180 AND 180",
            name=op.f("ck_user_preferences_home_longitude_range"),
        ),
        sa.CheckConstraint(
            "recommendation_scope IN ('local', 'international', 'both')",
            name=op.f("ck_user_preferences_recommendation_scope"),
        ),
        sa.CheckConstraint(
            "travel_style IS NULL OR travel_style IN "
            "('beaches', 'adventure', 'food', 'luxury', 'nature', 'culture')",
            name=op.f("ck_user_preferences_travel_style"),
        ),
        sa.CheckConstraint(
            "trip_pace IS NULL OR trip_pace IN ('relaxed', 'balanced', 'packed')",
            name=op.f("ck_user_preferences_trip_pace"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.users.id"],
            name=op.f("fk_user_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_preferences")),
        schema="app",
    )
    op.create_table(
        "user_interests",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("interest", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "interest IN ('hiking', 'photography', 'nightlife', 'wellness', "
            "'history', 'wildlife', 'shopping', 'local_culture', 'events')",
            name=op.f("ck_user_interests_interest"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user_preferences.user_id"],
            name=op.f("fk_user_interests_user_id_user_preferences"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "interest",
            name=op.f("pk_user_interests"),
        ),
        schema="app",
    )


def downgrade() -> None:
    """Drop normalized user-preference storage."""

    op.drop_table("user_interests", schema="app")
    op.drop_table("user_preferences", schema="app")
