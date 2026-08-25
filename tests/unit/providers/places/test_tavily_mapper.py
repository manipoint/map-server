"""Tests for deterministic Tavily place-search request building."""

import pytest

from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.schemas import PlaceSearchInput
from app.providers.places.tavily_mapper import (
    TAVILY_QUERY_MAX_LENGTH,
    build_tavily_place_search,
)


def create_location(
    *, display_name: str = "London, United Kingdom"
) -> ResolvedLocation:
    """Create one deterministic resolved destination."""

    return ResolvedLocation(
        query="London",
        display_name=display_name,
        latitude=51.5071,
        longitude=-0.1276,
    )


def test_build_tavily_search_uses_resolved_destination_and_safe_defaults() -> None:
    """The provider query should use the selected location and fixed cost controls."""

    provider_request = build_tavily_place_search(
        request=PlaceSearchInput(destination="London", max_results=3),
        location=create_location(),
    )

    assert provider_request.query == ("Best places to visit in London, United Kingdom.")
    assert provider_request.max_results == 3
    assert provider_request.search_depth == "basic"
    assert provider_request.include_answer is False
    assert provider_request.include_raw_content is False
    assert provider_request.include_images is False
    assert provider_request.auto_parameters is False


def test_build_tavily_search_preserves_interests_and_family_filter() -> None:
    """Interests should retain order and family intent in one compact query."""

    provider_request = build_tavily_place_search(
        request=PlaceSearchInput(
            destination="London",
            interests=["Museums", "parks", "museums"],
            family_friendly=True,
        ),
        location=create_location(),
    )

    assert provider_request.query == (
        "Best places to visit in London, United Kingdom. "
        "Interests: Museums, parks Family-friendly places only."
    )


@pytest.mark.parametrize("family_friendly", [None, False])
def test_build_tavily_search_omits_unrequested_family_filter(
    family_friendly: bool | None,
) -> None:
    """Unspecified or false family preference should not narrow discovery."""

    provider_request = build_tavily_place_search(
        request=PlaceSearchInput(
            destination="London",
            family_friendly=family_friendly,
        ),
        location=create_location(),
    )

    assert "Family-friendly" not in provider_request.query


def test_build_tavily_search_truncates_interests_at_query_boundary() -> None:
    """Long preference lists should remain valid without truncating an interest."""

    interests = [f"interest-{index}-{'x' * 60}" for index in range(10)]
    provider_request = build_tavily_place_search(
        request=PlaceSearchInput(
            destination="London",
            interests=interests,
            family_friendly=True,
            max_results=5,
        ),
        location=create_location(display_name="L" * 200),
    )

    assert len(provider_request.query) <= TAVILY_QUERY_MAX_LENGTH
    included = [item for item in interests if item in provider_request.query]
    assert included
    assert len(included) < len(interests)
    assert included == interests[: len(included)]
    assert "Family-friendly places only." in provider_request.query
