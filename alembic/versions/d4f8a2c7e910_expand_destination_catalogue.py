"""expand destination catalogue and normalize selected travel styles

Revision ID: d4f8a2c7e910
Revises: 6a1d7c9e4b20
Create Date: 2026-09-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4f8a2c7e910"
down_revision: str | Sequence[str] | None = "6a1d7c9e4b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add bucket-ready destination details, places, media, and style selections."""

    op.create_table(
        "user_travel_styles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("travel_style", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "travel_style IN "
            "('beaches', 'adventure', 'food', 'luxury', 'nature', 'culture')",
            name=op.f("ck_user_travel_styles_travel_style"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user_preferences.user_id"],
            name=op.f("fk_user_travel_styles_user_id_user_preferences"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "travel_style",
            name=op.f("pk_user_travel_styles"),
        ),
        schema="app",
    )
    op.execute(
        "INSERT INTO app.user_travel_styles (user_id, travel_style) "
        "SELECT user_id, travel_style FROM app.user_preferences "
        "WHERE travel_style IS NOT NULL"
    )
    op.drop_constraint(
        op.f("ck_user_preferences_travel_style"),
        "user_preferences",
        schema="app",
        type_="check",
    )
    op.drop_column("user_preferences", "travel_style", schema="app")

    op.add_column(
        "destinations",
        sa.Column(
            "destination_type",
            sa.String(length=32),
            server_default="city",
            nullable=False,
        ),
        schema="app",
    )
    op.add_column(
        "destinations",
        sa.Column("full_description", sa.Text(), nullable=True),
        schema="app",
    )
    op.add_column(
        "destinations",
        sa.Column("map_zoom", sa.Integer(), server_default="10", nullable=False),
        schema="app",
    )
    op.execute("UPDATE app.destinations SET full_description = summary")
    op.alter_column(
        "destinations",
        "full_description",
        existing_type=sa.Text(),
        nullable=False,
        schema="app",
    )
    op.create_check_constraint(
        op.f("ck_destinations_destination_type"),
        "destinations",
        "destination_type IN ('city', 'region', 'island', 'country')",
        schema="app",
    )
    op.create_check_constraint(
        op.f("ck_destinations_full_description_length"),
        "destinations",
        "char_length(btrim(full_description)) BETWEEN 20 AND 5000",
        schema="app",
    )
    op.create_check_constraint(
        op.f("ck_destinations_map_zoom_range"),
        "destinations",
        "map_zoom BETWEEN 1 AND 20",
        schema="app",
    )
    op.alter_column(
        "destinations",
        "destination_type",
        server_default=None,
        schema="app",
    )
    op.alter_column(
        "destinations",
        "map_zoom",
        server_default=None,
        schema="app",
    )
    op.execute(
        "UPDATE app.destinations SET destination_type = CASE slug "
        "WHEN 'hunza-pakistan' THEN 'region' "
        "WHEN 'skardu-pakistan' THEN 'region' "
        "WHEN 'bali-indonesia' THEN 'island' "
        "ELSE 'city' END"
    )
    op.execute(
        "UPDATE app.destinations SET map_zoom = CASE slug "
        "WHEN 'lahore-pakistan' THEN 11 "
        "WHEN 'hunza-pakistan' THEN 9 "
        "WHEN 'skardu-pakistan' THEN 9 "
        "WHEN 'istanbul-turkiye' THEN 11 "
        "WHEN 'bali-indonesia' THEN 9 "
        "WHEN 'paris-france' THEN 11 "
        "ELSE 10 END"
    )
    op.execute(
        "UPDATE app.destinations SET full_description = CASE slug "
        "WHEN 'skardu-pakistan' THEN 'Skardu is a mountain destination and a "
        "base for exploring alpine lakes, historic forts, valleys, cold desert "
        "landscapes, and routes toward the high Karakoram.' "
        "WHEN 'hunza-pakistan' THEN 'Hunza is a mountain region known for broad "
        "valley views, village communities, historic forts, scenic roads, and "
        "access to hiking and photography locations.' "
        "WHEN 'bali-indonesia' THEN 'Bali is an Indonesian island destination "
        "combining beaches, temples, rice landscapes, local communities, wellness "
        "experiences, and a varied food scene.' "
        "ELSE summary END"
    )

    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("alt_text", sa.String(length=200), nullable=False),
        sa.Column("caption", sa.String(length=500), nullable=True),
        sa.Column("credit_name", sa.String(length=200), nullable=True),
        sa.Column("license_info", sa.String(length=500), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
            "(width IS NULL AND height IS NULL) OR (width > 0 AND height > 0)",
            name=op.f("ck_media_assets_dimensions_consistent"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(alt_text)) BETWEEN 2 AND 200",
            name=op.f("ck_media_assets_alt_text_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_assets")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_media_assets_storage_key")),
        schema="app",
    )
    op.create_table(
        "destination_places",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("place_type", sa.String(length=50), nullable=False),
        sa.Column("summary", sa.String(length=600), nullable=False),
        sa.Column("full_description", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "is_featured",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_published",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
            "char_length(slug) BETWEEN 2 AND 120 AND slug = lower(slug)",
            name=op.f("ck_destination_places_slug_format"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(summary)) BETWEEN 20 AND 600",
            name=op.f("ck_destination_places_summary_length"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(full_description)) BETWEEN 20 AND 5000",
            name=op.f("ck_destination_places_full_description_length"),
        ),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name=op.f("ck_destination_places_latitude_range"),
        ),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name=op.f("ck_destination_places_longitude_range"),
        ),
        sa.CheckConstraint(
            "sort_order > 0",
            name=op.f("ck_destination_places_sort_order_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["app.destinations.id"],
            name=op.f("fk_destination_places_destination_id_destinations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_destination_places")),
        sa.UniqueConstraint(
            "destination_id",
            "slug",
            name="uq_destination_places_destination_slug",
        ),
        sa.UniqueConstraint(
            "destination_id",
            "sort_order",
            name="uq_destination_places_destination_sort_order",
        ),
        schema="app",
    )
    op.create_index(
        "ix_destination_places_published_order",
        "destination_places",
        ["destination_id", "is_published", "sort_order", "id"],
        schema="app",
    )
    op.execute(
        "INSERT INTO app.destination_places "
        "(id, destination_id, slug, name, place_type, summary, "
        "full_description, latitude, longitude, address, sort_order, "
        "is_featured, is_published) VALUES "
        "('30000000-0000-4000-8000-000000000001', "
        "'10000000-0000-4000-8000-000000000003', "
        "'upper-kachura-lake', 'Upper Kachura Lake', 'lake', "
        "'A high-altitude lake in Skardu District surrounded by mountain scenery.', "
        "'Upper Kachura Lake is a high-altitude lake in Skardu District and a "
        "curated stop for lake views, mountain scenery, and photography.', "
        "35.446389, 75.445833, 'Kachura, Skardu District', 1, true, true), "
        "('30000000-0000-4000-8000-000000000002', "
        "'10000000-0000-4000-8000-000000000003', "
        "'satpara-lake', 'Satpara Lake', 'lake', "
        "'A mountain lake south of Skardu that also supplies water to the valley.', "
        "'Satpara Lake lies south of Skardu among mountain terrain and feeds the "
        "Satpara Stream, which supplies water to Skardu Valley.', "
        "35.23389, 75.63139, 'Satpara, Skardu District', 2, true, true), "
        "('30000000-0000-4000-8000-000000000003', "
        "'10000000-0000-4000-8000-000000000003', "
        "'kharpocho-fort', 'Kharpocho Fort', 'fort', "
        "'A historic fort on a rocky height overlooking Skardu and the Indus.', "
        "'Kharpocho Fort stands above Skardu on the Khardong hill and provides a "
        "historic viewpoint over the city and surrounding river landscape.', "
        "35.301817, 75.643133, 'Khardong Hill, Skardu', 3, true, true), "
        "('30000000-0000-4000-8000-000000000004', "
        "'10000000-0000-4000-8000-000000000003', "
        "'katpana-cold-desert', 'Katpana Cold Desert', 'desert', "
        "'A high-altitude cold-desert landscape with dunes near Skardu.', "
        "'Katpana Cold Desert is a high-altitude desert near Skardu where sand "
        "dunes sit within a broad mountain landscape.', "
        "35.310522, 75.590747, 'Katpana, Skardu District', 4, false, true)"
    )
    op.create_table(
        "destination_media",
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("media_asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "role IN ('cover', 'gallery')",
            name=op.f("ck_destination_media_role"),
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name=op.f("ck_destination_media_sort_order_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["app.destinations.id"],
            name=op.f("fk_destination_media_destination_id_destinations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["app.media_assets.id"],
            name=op.f("fk_destination_media_media_asset_id_media_assets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "destination_id",
            "media_asset_id",
            name=op.f("pk_destination_media"),
        ),
        sa.UniqueConstraint(
            "destination_id",
            "sort_order",
            name="uq_destination_media_destination_sort_order",
        ),
        schema="app",
    )
    op.create_index(
        "uq_destination_media_one_cover",
        "destination_media",
        ["destination_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("role = 'cover'"),
    )
    op.create_table(
        "destination_place_media",
        sa.Column("destination_place_id", sa.Uuid(), nullable=False),
        sa.Column("media_asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "role IN ('cover', 'gallery')",
            name=op.f("ck_destination_place_media_role"),
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name=op.f("ck_destination_place_media_sort_order_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_place_id"],
            ["app.destination_places.id"],
            name=op.f(
                "fk_destination_place_media_destination_place_id_destination_places"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["app.media_assets.id"],
            name=op.f("fk_destination_place_media_media_asset_id_media_assets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "destination_place_id",
            "media_asset_id",
            name=op.f("pk_destination_place_media"),
        ),
        sa.UniqueConstraint(
            "destination_place_id",
            "sort_order",
            name="uq_destination_place_media_place_sort_order",
        ),
        schema="app",
    )
    op.create_index(
        "uq_destination_place_media_one_cover",
        "destination_place_media",
        ["destination_place_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("role = 'cover'"),
    )

    media_id = (
        "(substr(md5('destination-cover:' || id::text), 1, 8) || '-' || "
        "substr(md5('destination-cover:' || id::text), 9, 4) || '-' || "
        "substr(md5('destination-cover:' || id::text), 13, 4) || '-' || "
        "substr(md5('destination-cover:' || id::text), 17, 4) || '-' || "
        "substr(md5('destination-cover:' || id::text), 21, 12))::uuid"
    )
    op.execute(
        f"INSERT INTO app.media_assets "
        f"(id, url, mime_type, alt_text, is_active) "
        f"SELECT {media_id}, image_url, 'image/jpeg', image_alt, true "
        f"FROM app.destinations"
    )
    op.execute(
        f"INSERT INTO app.destination_media "
        f"(destination_id, media_asset_id, role, sort_order) "
        f"SELECT id, {media_id}, 'cover', 0 FROM app.destinations"
    )
    op.drop_column("destinations", "image_alt", schema="app")
    op.drop_column("destinations", "image_url", schema="app")


def downgrade() -> None:
    """Restore legacy single-style and single-image destination storage."""

    op.add_column(
        "destinations",
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        schema="app",
    )
    op.add_column(
        "destinations",
        sa.Column("image_alt", sa.String(length=200), nullable=True),
        schema="app",
    )
    op.execute(
        "UPDATE app.destinations AS d SET "
        "image_url = COALESCE(m.url, ''), "
        "image_alt = COALESCE(m.alt_text, d.name) "
        "FROM app.destination_media AS dm "
        "JOIN app.media_assets AS m ON m.id = dm.media_asset_id "
        "WHERE dm.destination_id = d.id AND dm.role = 'cover'"
    )
    op.execute(
        "UPDATE app.destinations SET image_url = '', image_alt = name "
        "WHERE image_url IS NULL"
    )
    op.alter_column(
        "destinations",
        "image_url",
        existing_type=sa.String(length=2048),
        nullable=False,
        schema="app",
    )
    op.alter_column(
        "destinations",
        "image_alt",
        existing_type=sa.String(length=200),
        nullable=False,
        schema="app",
    )

    op.drop_index(
        "uq_destination_place_media_one_cover",
        table_name="destination_place_media",
        schema="app",
    )
    op.drop_table("destination_place_media", schema="app")
    op.drop_index(
        "uq_destination_media_one_cover",
        table_name="destination_media",
        schema="app",
    )
    op.drop_table("destination_media", schema="app")
    op.drop_index(
        "ix_destination_places_published_order",
        table_name="destination_places",
        schema="app",
    )
    op.drop_table("destination_places", schema="app")
    op.drop_table("media_assets", schema="app")

    op.drop_constraint(
        op.f("ck_destinations_map_zoom_range"),
        "destinations",
        schema="app",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_destinations_full_description_length"),
        "destinations",
        schema="app",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_destinations_destination_type"),
        "destinations",
        schema="app",
        type_="check",
    )
    op.drop_column("destinations", "map_zoom", schema="app")
    op.drop_column("destinations", "full_description", schema="app")
    op.drop_column("destinations", "destination_type", schema="app")

    op.add_column(
        "user_preferences",
        sa.Column("travel_style", sa.String(length=32), nullable=True),
        schema="app",
    )
    op.execute(
        "UPDATE app.user_preferences AS preference SET travel_style = ("
        "SELECT min(selection.travel_style) "
        "FROM app.user_travel_styles AS selection "
        "WHERE selection.user_id = preference.user_id)"
    )
    op.create_check_constraint(
        op.f("ck_user_preferences_travel_style"),
        "user_preferences",
        "travel_style IS NULL OR travel_style IN "
        "('beaches', 'adventure', 'food', 'luxury', 'nature', 'culture')",
        schema="app",
    )
    op.drop_table("user_travel_styles", schema="app")
