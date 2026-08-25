"""Tests for Google Places request building and response normalization."""

from datetime import UTC, datetime

import pytest

from app.domain.places import PlaceSearchStatus
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.google_mapper import (
    GOOGLE_PLACES_MAX_QUERY_INTERESTS,
    GOOGLE_PLACES_MAX_RESULTS,
    GOOGLE_PLACES_QUERY_MAX_LENGTH,
    build_google_place_search,
    map_google_place,
    map_google_place_search_response,
    normalize_google_categories,
)
from app.providers.places.google_schemas import (
    GoogleLocalizedText,
    GooglePlaceResponse,
    GooglePlaceTextSearchResponse,
)
from app.providers.places.schemas import (
    PlaceSearchInput,
    ResolvedPlaceSearch,
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


def create_search(*, max_results: int = 5) -> ResolvedPlaceSearch:
    """Create one provider-ready place search."""

    return ResolvedPlaceSearch(
        request=PlaceSearchInput(
            destination="London",
            max_results=max_results,
        ),
        location=create_location(),
    )


def create_google_place(
    *,
    place_id: str = "google-place-123",
    name: str = "British Museum",
    google_maps_uri: str | None = "https://maps.google.com/?cid=123",
) -> GooglePlaceResponse:
    """Create one minimal canonical Google place."""

    return GooglePlaceResponse(
        id=place_id,
        display_name=GoogleLocalizedText(text=name, language_code="en"),
        formatted_address="Great Russell Street, London",
        location={"latitude": 51.5194, "longitude": -0.1270},
        primary_type="museum",
        types=["museum", "tourist_attraction", "point_of_interest"],
        google_maps_uri=google_maps_uri,
    )


def test_build_google_search_uses_safe_defaults() -> None:
    """A general search should use one resolved destination and fixed defaults."""

    provider_request = build_google_place_search(
        request=PlaceSearchInput(destination="London", max_results=3),
        location=create_location(),
    )

    assert provider_request.text_query == (
        "Tourist attractions in London, United Kingdom"
    )
    assert provider_request.page_size == 3
    assert provider_request.language_code == "en"
    assert provider_request.rank_preference == "RELEVANCE"
    assert provider_request.included_type is None


def test_build_google_search_accepts_maximum_result_limit() -> None:
    """One billable request should use the shared maximum result limit."""

    provider_request = build_google_place_search(
        request=PlaceSearchInput(destination="London", max_results=5),
        location=create_location(),
    )

    assert provider_request.page_size == GOOGLE_PLACES_MAX_RESULTS


def test_build_google_search_limits_interests_and_preserves_order() -> None:
    """Only the first bounded interests should be included in one query."""

    interests = ["museums", "parks", "history", "shopping"]
    provider_request = build_google_place_search(
        request=PlaceSearchInput(
            destination="London",
            interests=interests,
        ),
        location=create_location(),
    )

    included = interests[:GOOGLE_PLACES_MAX_QUERY_INTERESTS]
    assert ", ".join(included) in provider_request.text_query
    assert interests[GOOGLE_PLACES_MAX_QUERY_INTERESTS] not in (
        provider_request.text_query
    )


def test_build_google_search_preserves_family_intent() -> None:
    """Family-friendly intent should narrow the same single provider query."""

    provider_request = build_google_place_search(
        request=PlaceSearchInput(
            destination="London",
            interests=["museums"],
            family_friendly=True,
        ),
        location=create_location(),
    )

    assert provider_request.text_query.startswith("Family-friendly ")
    assert "museums" in provider_request.text_query


def test_build_google_search_stays_within_provider_query_limit() -> None:
    """Long validated inputs should still produce a provider-safe query."""

    provider_request = build_google_place_search(
        request=PlaceSearchInput(
            destination="London",
            interests=["x" * 80 for _ in range(3)],
        ),
        location=create_location(display_name="L" * 200),
    )

    assert len(provider_request.text_query) <= GOOGLE_PLACES_QUERY_MAX_LENGTH


def test_normalize_google_categories_preserves_useful_unique_types() -> None:
    """Provider types should become bounded display categories without noise."""

    categories = normalize_google_categories(create_google_place())

    assert categories == ["museum", "tourist attraction"]


def test_map_google_place_preserves_identity_coordinates_and_evidence() -> None:
    """One canonical record should retain fields needed by Flutter clients."""

    place = map_google_place(
        place=create_google_place(),
        location=create_location(),
    )

    assert place is not None
    assert place.provider_place_id == "google-place-123"
    assert place.name == "British Museum"
    assert place.categories == ["museum", "tourist attraction"]
    assert place.latitude == 51.5194
    assert place.longitude == -0.1270
    assert [str(url) for url in place.source_urls] == [
        "https://maps.google.com/?cid=123"
    ]
    assert "Great Russell Street" in place.summary


def test_map_google_place_skips_record_without_evidence_url() -> None:
    """A result without its requested Google Maps URI should not be exposed."""

    place = map_google_place(
        place=create_google_place(google_maps_uri=None),
        location=create_location(),
    )

    assert place is None


def test_map_google_response_preserves_order_deduplicates_and_caps() -> None:
    """Normalization should retain provider ranking within the request limit."""

    response = GooglePlaceTextSearchResponse(
        places=[
            create_google_place(place_id="place-1", name="First"),
            create_google_place(place_id="place-1", name="Duplicate"),
            create_google_place(place_id="place-2", name="Second"),
            create_google_place(place_id="place-3", name="Third"),
        ]
    )

    result = map_google_place_search_response(
        response=response,
        search=create_search(max_results=2),
        searched_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
    )

    assert result.status is PlaceSearchStatus.PLACES_AVAILABLE
    assert [place.name for place in result.places] == ["First", "Second"]


def test_map_google_response_returns_no_places_for_unusable_records() -> None:
    """An empty usable result set should become a valid no-places outcome."""

    result = map_google_place_search_response(
        response=GooglePlaceTextSearchResponse(
            places=[create_google_place(google_maps_uri=None)]
        ),
        search=create_search(),
        searched_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
    )

    assert result.status is PlaceSearchStatus.NO_PLACES
    assert result.places == []
    assert result.message == "No relevant places were found for this search."


def test_map_google_response_rejects_naive_timestamp() -> None:
    """Provider results must use a timezone-aware application timestamp."""

    with pytest.raises(ValueError, match="searched_at must include a timezone"):
        map_google_place_search_response(
            response=GooglePlaceTextSearchResponse(),
            search=create_search(),
            searched_at=datetime(2026, 8, 25, 12),
        )
