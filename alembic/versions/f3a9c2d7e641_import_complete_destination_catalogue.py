"""Import the complete reviewed destination catalogue and bucket media.

Revision ID: f3a9c2d7e641
Revises: b7e2f9a41063
"""

import json
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa

from alembic import op

revision = "f3a9c2d7e641"
down_revision = "b7e2f9a41063"
branch_labels = None
depends_on = None

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "f3a9c2d7e641_catalogue.json"


def _manifest() -> dict:
    document = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise RuntimeError("Unsupported destination catalogue manifest")
    expected = (17, 104, 224)
    actual = tuple(len(document[key]) for key in ("destinations", "places", "media"))
    if actual != expected:
        raise RuntimeError(f"Invalid destination catalogue manifest counts: {actual}")
    return document


def upgrade() -> None:
    """Insert catalogue records and media links in one transaction."""

    document = _manifest()
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    connection.execute(sa.text("SET LOCAL statement_timeout = '60s'"))
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(505317, 2)"))

    destination_slugs = {row["slug"] for row in document["destinations"]}
    place_parent_slugs = {row["destination_slug"] for row in document["places"]}
    all_parent_slugs = destination_slugs | place_parent_slugs
    existing = dict(
        connection.execute(
            sa.text("SELECT slug, id FROM app.destinations WHERE slug = ANY(:slugs)"),
            {"slugs": sorted(all_parent_slugs)},
        ).all()
    )
    unexpected = destination_slugs & existing.keys()
    missing_parents = (place_parent_slugs - destination_slugs) - existing.keys()
    if unexpected or missing_parents:
        raise RuntimeError(
            "Catalogue precondition failed; existing new slugs="
            f"{sorted(unexpected)}, missing parents={sorted(missing_parents)}"
        )

    destination_rows = []
    style_rows = []
    interest_rows = []
    for row in document["destinations"]:
        destination_id = row["id"]
        destination_rows.append(
            {
                key: row[key]
                for key in (
                    "id",
                    "slug",
                    "name",
                    "destination_type",
                    "country_name",
                    "country_code",
                    "summary",
                    "full_description",
                    "latitude",
                    "longitude",
                    "map_zoom",
                    "budget_tier",
                    "is_published",
                    "is_featured",
                    "featured_rank",
                    "is_popular",
                    "popular_rank",
                    "editorial_rank",
                )
            }
        )
        style_rows.extend(
            {"destination_id": destination_id, "style": style}
            for style in row["styles"]
        )
        interest_rows.extend(
            {"destination_id": destination_id, "interest": interest}
            for interest in row["interests"]
        )

    connection.execute(
        sa.text("""
            INSERT INTO app.destinations (
                id, slug, name, destination_type, country_name, country_code,
                summary, full_description, latitude, longitude, map_zoom,
                budget_tier, is_published, is_featured, featured_rank,
                is_popular, popular_rank, editorial_rank
            ) VALUES (
                :id, :slug, :name, :destination_type, :country_name, :country_code,
                :summary, :full_description, :latitude, :longitude, :map_zoom,
                :budget_tier, :is_published, :is_featured, :featured_rank,
                :is_popular, :popular_rank, :editorial_rank
            )
        """),
        destination_rows,
    )
    existing.update({row["slug"]: row["id"] for row in document["destinations"]})
    connection.execute(
        sa.text(
            "INSERT INTO app.destination_styles (destination_id, style) VALUES (:destination_id, :style)"
        ),
        style_rows,
    )
    connection.execute(
        sa.text(
            "INSERT INTO app.destination_interests (destination_id, interest) VALUES (:destination_id, :interest)"
        ),
        interest_rows,
    )

    place_rows = [
        {
            **{
                key: row[key]
                for key in (
                    "id",
                    "slug",
                    "name",
                    "place_type",
                    "summary",
                    "full_description",
                    "latitude",
                    "longitude",
                    "address",
                    "sort_order",
                    "is_featured",
                    "is_published",
                )
            },
            "destination_id": existing[row["destination_slug"]],
        }
        for row in document["places"]
    ]
    connection.execute(
        sa.text("""
            INSERT INTO app.destination_places (
                id, destination_id, slug, name, place_type, summary,
                full_description, latitude, longitude, address, sort_order,
                is_featured, is_published
            ) VALUES (
                :id, :destination_id, :slug, :name, :place_type, :summary,
                :full_description, :latitude, :longitude, :address, :sort_order,
                :is_featured, :is_published
            )
        """),
        place_rows,
    )

    media_rows = [
        {
            key: row[key]
            for key in (
                "id",
                "storage_key",
                "url",
                "mime_type",
                "alt_text",
                "license_info",
            )
        }
        for row in document["media"]
    ]
    connection.execute(
        sa.text("""
            INSERT INTO app.media_assets (
                id, storage_key, url, mime_type, alt_text, license_info, is_active
            ) VALUES (
                :id, :storage_key, :url, :mime_type, :alt_text, :license_info, true
            )
        """),
        media_rows,
    )
    destination_media = [
        {
            "destination_id": row["owner_id"],
            "media_asset_id": row["id"],
            "role": row["role"],
            "sort_order": row["sort_order"],
        }
        for row in document["media"]
        if row["owner_type"] == "destination"
    ]
    place_media = [
        {
            "destination_place_id": row["owner_id"],
            "media_asset_id": row["id"],
            "role": row["role"],
            "sort_order": row["sort_order"],
        }
        for row in document["media"]
        if row["owner_type"] == "place"
    ]
    connection.execute(
        sa.text("""
            INSERT INTO app.destination_media (
                destination_id, media_asset_id, role, sort_order
            ) VALUES (
                :destination_id, :media_asset_id, :role, :sort_order
            )
        """),
        destination_media,
    )
    connection.execute(
        sa.text("""
            INSERT INTO app.destination_place_media (
                destination_place_id, media_asset_id, role, sort_order
            ) VALUES (
                :destination_place_id, :media_asset_id, :role, :sort_order
            )
        """),
        place_media,
    )


def downgrade() -> None:
    """Delete only records owned by this immutable import manifest."""

    document = _manifest()
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(505317, 2)"))
    connection.execute(
        sa.text("DELETE FROM app.media_assets WHERE id = ANY(:ids)"),
        {"ids": [UUID(row["id"]) for row in document["media"]]},
    )
    connection.execute(
        sa.text("DELETE FROM app.destination_places WHERE id = ANY(:ids)"),
        {"ids": [UUID(row["id"]) for row in document["places"]]},
    )
    connection.execute(
        sa.text("DELETE FROM app.destinations WHERE id = ANY(:ids)"),
        {"ids": [UUID(row["id"]) for row in document["destinations"]]},
    )
