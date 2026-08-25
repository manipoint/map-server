"""Tests for shared deterministic location selection."""

import pytest

from app.common.exceptions import AmbiguousLocationError, LocationNotFoundError
from app.providers.locations.schemas import ResolvedLocation
from app.services.location_selection import select_resolved_location


def create_location(display_name: str, latitude: float) -> ResolvedLocation:
    """Create one location candidate with a unique latitude."""

    return ResolvedLocation(
        query="London",
        display_name=display_name,
        latitude=latitude,
        longitude=-0.1276,
    )


def test_select_resolved_location_returns_only_candidate() -> None:
    """One candidate should be safe even when the query is less specific."""

    location = create_location("London, United Kingdom", 51.5071)

    assert select_resolved_location(query="London", candidates=[location]) is location


def test_select_resolved_location_accepts_exact_first_match() -> None:
    """A fully qualified query should select the provider's first exact result."""

    london_uk = create_location("London, United Kingdom", 51.5071)
    london_ca = create_location("London, Ontario, Canada", 42.9849)

    selected = select_resolved_location(
        query="london, united kingdom",
        candidates=[london_uk, london_ca],
    )

    assert selected is london_uk


def test_select_resolved_location_rejects_ambiguous_candidates() -> None:
    """A short query should return candidate names instead of guessing."""

    candidates = [
        create_location("London, United Kingdom", 51.5071),
        create_location("London, Ontario, Canada", 42.9849),
    ]

    with pytest.raises(AmbiguousLocationError) as caught:
        select_resolved_location(query="London", candidates=candidates)

    assert caught.value.candidates == (
        "London, United Kingdom",
        "London, Ontario, Canada",
    )


def test_select_resolved_location_rejects_empty_candidates() -> None:
    """No provider matches should become a shared not-found error."""

    with pytest.raises(LocationNotFoundError, match="No location matched"):
        select_resolved_location(query="Unknown", candidates=[])
