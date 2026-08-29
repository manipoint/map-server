"""Add bounded structured assistant response content.

Revision ID: a61c9e4b2d77
Revises: 91d3f4a6c8b2
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a61c9e4b2d77"
down_revision: str | Sequence[str] | None = "91d3f4a6c8b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add optional structured content to conversation messages."""

    op.add_column(
        "messages",
        sa.Column("structured_content", sa.JSON(), nullable=True),
        schema="app",
    )
    op.create_check_constraint(
        "ck_messages_structured_content_assistant_only",
        "messages",
        "role = 'assistant' OR structured_content IS NULL",
        schema="app",
    )


def downgrade() -> None:
    """Remove optional structured content from conversation messages."""

    op.drop_constraint(
        "ck_messages_structured_content_assistant_only",
        "messages",
        schema="app",
        type_="check",
    )
    op.drop_column("messages", "structured_content", schema="app")
