"""normalize legacy check constraint names

Revision ID: f2a7c5d83b10
Revises: e8b4f2c91a60
Create Date: 2026-09-05 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2a7c5d83b10"
down_revision: str | Sequence[str] | None = "e8b4f2c91a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMES = {
    "messages": (
        (
            "ck_messages_ck_messages_structured_content_assistant_only",
            "ck_messages_structured_content_assistant_only",
        ),
    ),
    "trips": tuple(
        (
            f"ck_trips_ck_trips_{prefix}_{suffix}",
            f"ck_trips_{prefix}_{suffix}",
        )
        for prefix in ("origin", "destination")
        for suffix in (
            "location_complete",
            "latitude_range",
            "longitude_range",
        )
    ),
}


def _rename_constraints(*, reverse: bool) -> None:
    """Rename known constraints without rebuilding or relocking their tables."""

    for table_name, renames in RENAMES.items():
        for legacy_name, normalized_name in renames:
            source, target = (
                (normalized_name, legacy_name)
                if reverse
                else (legacy_name, normalized_name)
            )
            op.execute(
                f'ALTER TABLE app.{table_name} RENAME CONSTRAINT "{source}" '
                f'TO "{target}"'
            )


def upgrade() -> None:
    """Align legacy physical names with SQLAlchemy naming metadata."""

    _rename_constraints(reverse=False)


def downgrade() -> None:
    """Restore names expected by the original historical migrations."""

    _rename_constraints(reverse=True)
