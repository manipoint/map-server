"""update Pakistan destination images

Revision ID: 6a1d7c9e4b20
Revises: f2a7c5d83b10
Create Date: 2026-09-10 00:00:00.000000

"""

from collections.abc import Mapping, Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6a1d7c9e4b20"
down_revision: str | Sequence[str] | None = "f2a7c5d83b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_IMAGE_URLS = {
    "lahore-pakistan": (
        "https://images.unsplash.com/photo-1587474260584-136574528ed5"
        "?auto=format&fit=crop&w=900&q=75"
    ),
    "hunza-pakistan": (
        "https://images.unsplash.com/photo-1566837497312-7be4a6969925"
        "?auto=format&fit=crop&w=900&q=75"
    ),
    "skardu-pakistan": (
        "https://images.unsplash.com/photo-1622279488413-3e891fb3c9f1"
        "?auto=format&fit=crop&w=900&q=75"
    ),
}

NEW_IMAGE_URLS = {
    "lahore-pakistan": (
        "https://images.unsplash.com/photo-1684439061252-cb6632acb8ce"
        "?q=80&w=1674&auto=format&fit=crop&ixlib=rb-4.1.0&"
        "ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D"
    ),
    "hunza-pakistan": (
        "https://images.unsplash.com/photo-1672652787237-20f1cf66624e"
        "?q=80&w=1740&auto=format&fit=crop&ixlib=rb-4.1.0&"
        "ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D"
    ),
    "skardu-pakistan": (
        "https://travelot-live.s3-eu-west-1.amazonaws.com/"
        "upload/2022/08/16594276102498_shangrila4.jpg"
    ),
}


def _update_image_urls(image_urls: Mapping[str, str]) -> None:
    destinations = sa.table(
        "destinations",
        sa.column("slug", sa.String()),
        sa.column("image_url", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        schema="app",
    )

    connection = op.get_bind()
    for slug, image_url in image_urls.items():
        connection.execute(
            destinations.update()
            .where(destinations.c.slug == slug)
            .values(image_url=image_url, updated_at=sa.func.now())
        )


def upgrade() -> None:
    """Replace the Lahore, Hunza, and Skardu catalogue images."""

    _update_image_urls(NEW_IMAGE_URLS)


def downgrade() -> None:
    """Restore the previous catalogue images."""

    _update_image_urls(OLD_IMAGE_URLS)
