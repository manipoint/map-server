"""Tests for normalized curated destination metadata."""

from sqlalchemy import CheckConstraint

from app.database.models import (
    Destination,
    DestinationInterest,
    DestinationMedia,
    DestinationPlace,
    DestinationStyle,
    MediaAsset,
)


def test_destination_contains_publication_and_editorial_fields() -> None:
    """Catalogue rows should carry all stable Home card and ordering data."""

    assert Destination.__table__.schema == "app"
    assert set(Destination.__table__.columns.keys()) == {
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
        "created_at",
        "updated_at",
    }


def test_destination_enforces_consistent_curated_ranks() -> None:
    """Featured and Popular labels must always have deterministic ordering."""

    names = {
        constraint.name
        for constraint in Destination.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_destinations_featured_rank_consistent" in names
    assert "ck_destinations_popular_rank_consistent" in names
    assert "ck_destinations_budget_tier" in names


def test_destination_tags_use_normalized_composite_keys() -> None:
    """Styles and interests should be queryable rows rather than JSON blobs."""

    style_key = tuple(
        column.name for column in DestinationStyle.__table__.primary_key.columns
    )
    interest_key = tuple(
        column.name for column in DestinationInterest.__table__.primary_key.columns
    )
    style_fk = next(iter(DestinationStyle.__table__.c.destination_id.foreign_keys))
    interest_fk = next(
        iter(DestinationInterest.__table__.c.destination_id.foreign_keys)
    )

    assert style_key == ("destination_id", "style")
    assert interest_key == ("destination_id", "interest")
    assert style_fk.target_fullname == "app.destinations.id"
    assert interest_fk.target_fullname == "app.destinations.id"
    assert style_fk.ondelete == "CASCADE"
    assert interest_fk.ondelete == "CASCADE"


def test_destination_has_bounded_home_query_indexes() -> None:
    """Published editorial sections should not require unordered table scans."""

    names = {index.name for index in Destination.__table__.indexes}

    assert "ix_destinations_published_editorial" in names
    assert "ix_destinations_published_featured" in names
    assert "ix_destinations_published_popular" in names


def test_destination_places_and_media_are_normalized() -> None:
    """Galleries and nested places must not be stored as destination JSON."""

    assert DestinationPlace.__table__.schema == "app"
    assert MediaAsset.__table__.schema == "app"
    assert DestinationMedia.__table__.schema == "app"
    destination_fk = next(
        iter(DestinationMedia.__table__.c.destination_id.foreign_keys)
    )
    media_fk = next(iter(DestinationMedia.__table__.c.media_asset_id.foreign_keys))
    assert destination_fk.target_fullname == "app.destinations.id"
    assert media_fk.target_fullname == "app.media_assets.id"
    assert destination_fk.ondelete == "CASCADE"
    assert media_fk.ondelete == "CASCADE"
