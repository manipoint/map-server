"""Tests for SQLAlchemy database metadata."""

from app.database.base import NAMING_CONVENTION, Base


def test_base_uses_constraint_naming_convention() -> None:
    """Database metadata should expose deterministic constraint names."""
    assert Base.metadata.naming_convention is not None
    for key, value in NAMING_CONVENTION.items():
        assert Base.metadata.naming_convention[key] == value
