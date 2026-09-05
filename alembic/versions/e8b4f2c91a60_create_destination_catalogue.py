"""create and seed curated destination catalogue

Revision ID: e8b4f2c91a60
Revises: c3d9a4e71f20
Create Date: 2026-09-05 00:00:00.000000

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa

from alembic import op

revision: str = "e8b4f2c91a60"
down_revision: str | Sequence[str] | None = "c3d9a4e71f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTINATIONS = [
    {
        "id": "10000000-0000-4000-8000-000000000001",
        "slug": "lahore-pakistan",
        "name": "Lahore",
        "country_name": "Pakistan",
        "country_code": "PK",
        "summary": "Explore Mughal landmarks, historic bazaars, gardens, and Lahore's celebrated food culture.",
        "image_url": "https://images.unsplash.com/photo-1587474260584-136574528ed5?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Historic architecture in Lahore",
        "latitude": 31.5204,
        "longitude": 74.3587,
        "budget_tier": "budget",
        "is_published": True,
        "is_featured": True,
        "featured_rank": 4,
        "is_popular": True,
        "popular_rank": 5,
        "editorial_rank": 5,
    },
    {
        "id": "10000000-0000-4000-8000-000000000002",
        "slug": "hunza-pakistan",
        "name": "Hunza",
        "country_name": "Pakistan",
        "country_code": "PK",
        "summary": "Discover mountain valleys, scenic trails, welcoming villages, and dramatic Karakoram landscapes.",
        "image_url": "https://images.unsplash.com/photo-1566837497312-7be4a6969925?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Mountain valley in Hunza",
        "latitude": 36.3167,
        "longitude": 74.65,
        "budget_tier": "mid_range",
        "is_published": True,
        "is_featured": True,
        "featured_rank": 2,
        "is_popular": True,
        "popular_rank": 4,
        "editorial_rank": 2,
    },
    {
        "id": "10000000-0000-4000-8000-000000000003",
        "slug": "skardu-pakistan",
        "name": "Skardu",
        "country_name": "Pakistan",
        "country_code": "PK",
        "summary": "Plan an alpine escape around turquoise lakes, high-altitude deserts, forts, and trekking routes.",
        "image_url": "https://images.unsplash.com/photo-1622279488413-3e891fb3c9f1?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Lake and mountains near Skardu",
        "latitude": 35.2971,
        "longitude": 75.6333,
        "budget_tier": "mid_range",
        "is_published": True,
        "is_featured": False,
        "featured_rank": None,
        "is_popular": True,
        "popular_rank": 6,
        "editorial_rank": 6,
    },
    {
        "id": "10000000-0000-4000-8000-000000000004",
        "slug": "istanbul-turkiye",
        "name": "Istanbul",
        "country_name": "Türkiye",
        "country_code": "TR",
        "summary": "Experience grand mosques, Bosphorus views, layered history, lively markets, and rich cuisine.",
        "image_url": "https://images.unsplash.com/photo-1524231757912-21f4fe3a7200?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Istanbul skyline beside the Bosphorus",
        "latitude": 41.0082,
        "longitude": 28.9784,
        "budget_tier": "mid_range",
        "is_published": True,
        "is_featured": True,
        "featured_rank": 3,
        "is_popular": True,
        "popular_rank": 3,
        "editorial_rank": 3,
    },
    {
        "id": "10000000-0000-4000-8000-000000000005",
        "slug": "dubai-united-arab-emirates",
        "name": "Dubai",
        "country_name": "United Arab Emirates",
        "country_code": "AE",
        "summary": "Combine modern architecture, desert experiences, global dining, shopping, and luxury stays.",
        "image_url": "https://images.unsplash.com/photo-1512453979798-5ea266f8880c?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Dubai skyline at dusk",
        "latitude": 25.2048,
        "longitude": 55.2708,
        "budget_tier": "premium",
        "is_published": True,
        "is_featured": False,
        "featured_rank": None,
        "is_popular": True,
        "popular_rank": 2,
        "editorial_rank": 8,
    },
    {
        "id": "10000000-0000-4000-8000-000000000006",
        "slug": "bali-indonesia",
        "name": "Bali",
        "country_name": "Indonesia",
        "country_code": "ID",
        "summary": "Balance beaches, temples, rice terraces, wellness retreats, and creative local communities.",
        "image_url": "https://images.unsplash.com/photo-1537996194471-e657df975ab4?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Tropical temple landscape in Bali",
        "latitude": -8.4095,
        "longitude": 115.1889,
        "budget_tier": "mid_range",
        "is_published": True,
        "is_featured": True,
        "featured_rank": 1,
        "is_popular": True,
        "popular_rank": 1,
        "editorial_rank": 1,
    },
    {
        "id": "10000000-0000-4000-8000-000000000007",
        "slug": "tokyo-japan",
        "name": "Tokyo",
        "country_name": "Japan",
        "country_code": "JP",
        "summary": "Navigate historic districts, contemporary culture, exceptional food, shopping, and city parks.",
        "image_url": "https://images.unsplash.com/photo-1540959733332-eab4deabeeaf?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Tokyo city lights and streets",
        "latitude": 35.6762,
        "longitude": 139.6503,
        "budget_tier": "premium",
        "is_published": True,
        "is_featured": True,
        "featured_rank": 5,
        "is_popular": True,
        "popular_rank": 7,
        "editorial_rank": 7,
    },
    {
        "id": "10000000-0000-4000-8000-000000000008",
        "slug": "london-united-kingdom",
        "name": "London",
        "country_name": "United Kingdom",
        "country_code": "GB",
        "summary": "Mix renowned museums, royal landmarks, neighbourhood markets, theatre, and expansive parks.",
        "image_url": "https://images.unsplash.com/photo-1513635269975-59663e0ac1ad?auto=format&fit=crop&w=900&q=75",
        "image_alt": "London skyline and Westminster",
        "latitude": 51.5074,
        "longitude": -0.1278,
        "budget_tier": "premium",
        "is_published": True,
        "is_featured": True,
        "featured_rank": 6,
        "is_popular": False,
        "popular_rank": None,
        "editorial_rank": 9,
    },
    {
        "id": "10000000-0000-4000-8000-000000000009",
        "slug": "paris-france",
        "name": "Paris",
        "country_name": "France",
        "country_code": "FR",
        "summary": "Explore landmark architecture, art collections, neighbourhood cafés, gardens, and celebrated cuisine.",
        "image_url": "https://images.unsplash.com/photo-1502602898657-3e91760cbb34?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Eiffel Tower and Paris cityscape",
        "latitude": 48.8566,
        "longitude": 2.3522,
        "budget_tier": "premium",
        "is_published": True,
        "is_featured": False,
        "featured_rank": None,
        "is_popular": False,
        "popular_rank": None,
        "editorial_rank": 10,
    },
    {
        "id": "10000000-0000-4000-8000-000000000010",
        "slug": "bangkok-thailand",
        "name": "Bangkok",
        "country_name": "Thailand",
        "country_code": "TH",
        "summary": "Discover ornate temples, energetic street life, night markets, river journeys, and famous food.",
        "image_url": "https://images.unsplash.com/photo-1508009603885-50cf7c579365?auto=format&fit=crop&w=900&q=75",
        "image_alt": "Temple and city view in Bangkok",
        "latitude": 13.7563,
        "longitude": 100.5018,
        "budget_tier": "budget",
        "is_published": True,
        "is_featured": False,
        "featured_rank": None,
        "is_popular": False,
        "popular_rank": None,
        "editorial_rank": 4,
    },
]

STYLES = {
    1: ("culture", "food"),
    2: ("adventure", "nature"),
    3: ("adventure", "nature"),
    4: ("culture", "food"),
    5: ("luxury",),
    6: ("beaches", "culture", "nature"),
    7: ("culture", "food"),
    8: ("culture",),
    9: ("culture", "food", "luxury"),
    10: ("culture", "food"),
}

INTERESTS = {
    1: ("history", "local_culture", "shopping"),
    2: ("hiking", "photography", "wildlife"),
    3: ("hiking", "photography", "wildlife"),
    4: ("history", "local_culture", "shopping"),
    5: ("events", "nightlife", "shopping"),
    6: ("local_culture", "photography", "wellness"),
    7: ("events", "local_culture", "shopping"),
    8: ("events", "history", "shopping"),
    9: ("history", "local_culture", "shopping"),
    10: ("local_culture", "nightlife", "shopping"),
}


def upgrade() -> None:
    """Create normalized catalogue tables and deterministic starter content."""

    op.create_table(
        "destinations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("country_name", sa.String(length=120), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("summary", sa.String(length=600), nullable=False),
        sa.Column("image_url", sa.String(length=2048), nullable=False),
        sa.Column("image_alt", sa.String(length=200), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("budget_tier", sa.String(length=32), nullable=False),
        sa.Column(
            "is_published",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_featured",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("featured_rank", sa.Integer(), nullable=True),
        sa.Column(
            "is_popular",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("popular_rank", sa.Integer(), nullable=True),
        sa.Column("editorial_rank", sa.Integer(), nullable=False),
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
            "budget_tier IN ('budget', 'mid_range', 'premium', 'luxury')",
            name=op.f("ck_destinations_budget_tier"),
        ),
        sa.CheckConstraint(
            "char_length(country_code) = 2 AND country_code = upper(country_code)",
            name=op.f("ck_destinations_country_code_format"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(country_name)) BETWEEN 2 AND 120",
            name=op.f("ck_destinations_country_name_length"),
        ),
        sa.CheckConstraint(
            "editorial_rank > 0",
            name=op.f("ck_destinations_editorial_rank_positive"),
        ),
        sa.CheckConstraint(
            "(is_featured AND featured_rank IS NOT NULL AND featured_rank > 0) "
            "OR (NOT is_featured AND featured_rank IS NULL)",
            name=op.f("ck_destinations_featured_rank_consistent"),
        ),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name=op.f("ck_destinations_latitude_range"),
        ),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name=op.f("ck_destinations_longitude_range"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 2 AND 120",
            name=op.f("ck_destinations_name_length"),
        ),
        sa.CheckConstraint(
            "(is_popular AND popular_rank IS NOT NULL AND popular_rank > 0) "
            "OR (NOT is_popular AND popular_rank IS NULL)",
            name=op.f("ck_destinations_popular_rank_consistent"),
        ),
        sa.CheckConstraint(
            "char_length(slug) BETWEEN 2 AND 120 AND slug = lower(slug)",
            name=op.f("ck_destinations_slug_format"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(summary)) BETWEEN 20 AND 600",
            name=op.f("ck_destinations_summary_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_destinations")),
        sa.UniqueConstraint("slug", name=op.f("uq_destinations_slug")),
        schema="app",
    )
    op.create_index(
        "ix_destinations_published_editorial",
        "destinations",
        ["is_published", "editorial_rank"],
        unique=False,
        schema="app",
    )
    op.create_index(
        "ix_destinations_published_featured",
        "destinations",
        ["is_published", "is_featured", "featured_rank"],
        unique=False,
        schema="app",
    )
    op.create_index(
        "ix_destinations_published_popular",
        "destinations",
        ["is_published", "is_popular", "popular_rank"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "destination_styles",
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("style", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "style IN ('beaches', 'adventure', 'food', 'luxury', 'nature', 'culture')",
            name=op.f("ck_destination_styles_style"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["app.destinations.id"],
            name=op.f("fk_destination_styles_destination_id_destinations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "destination_id",
            "style",
            name=op.f("pk_destination_styles"),
        ),
        schema="app",
    )
    op.create_table(
        "destination_interests",
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("interest", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "interest IN ('hiking', 'photography', 'nightlife', 'wellness', "
            "'history', 'wildlife', 'shopping', 'local_culture', 'events')",
            name=op.f("ck_destination_interests_interest"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["app.destinations.id"],
            name=op.f("fk_destination_interests_destination_id_destinations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "destination_id",
            "interest",
            name=op.f("pk_destination_interests"),
        ),
        schema="app",
    )

    destination_table = sa.table(
        "destinations",
        sa.column("id", sa.Uuid()),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("country_name", sa.String()),
        sa.column("country_code", sa.String()),
        sa.column("summary", sa.String()),
        sa.column("image_url", sa.String()),
        sa.column("image_alt", sa.String()),
        sa.column("latitude", sa.Float()),
        sa.column("longitude", sa.Float()),
        sa.column("budget_tier", sa.String()),
        sa.column("is_published", sa.Boolean()),
        sa.column("is_featured", sa.Boolean()),
        sa.column("featured_rank", sa.Integer()),
        sa.column("is_popular", sa.Boolean()),
        sa.column("popular_rank", sa.Integer()),
        sa.column("editorial_rank", sa.Integer()),
        schema="app",
    )
    style_table = sa.table(
        "destination_styles",
        sa.column("destination_id", sa.Uuid()),
        sa.column("style", sa.String()),
        schema="app",
    )
    interest_table = sa.table(
        "destination_interests",
        sa.column("destination_id", sa.Uuid()),
        sa.column("interest", sa.String()),
        schema="app",
    )
    seed_destinations = [
        {**destination, "id": UUID(destination["id"])} for destination in DESTINATIONS
    ]
    op.bulk_insert(destination_table, seed_destinations)
    op.bulk_insert(
        style_table,
        [
            {
                "destination_id": destination["id"],
                "style": style,
            }
            for index, destination in enumerate(seed_destinations, start=1)
            for style in STYLES[index]
        ],
    )
    op.bulk_insert(
        interest_table,
        [
            {
                "destination_id": destination["id"],
                "interest": interest,
            }
            for index, destination in enumerate(seed_destinations, start=1)
            for interest in INTERESTS[index]
        ],
    )


def downgrade() -> None:
    """Remove the curated destination catalogue."""

    op.drop_table("destination_interests", schema="app")
    op.drop_table("destination_styles", schema="app")
    op.drop_index(
        "ix_destinations_published_popular",
        table_name="destinations",
        schema="app",
    )
    op.drop_index(
        "ix_destinations_published_featured",
        table_name="destinations",
        schema="app",
    )
    op.drop_index(
        "ix_destinations_published_editorial",
        table_name="destinations",
        schema="app",
    )
    op.drop_table("destinations", schema="app")
