"""Tests for provider-independent place-discovery schemas."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.places import PlaceSearchStatus
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.schemas import (
    PlaceOption,
    PlaceSearchInput,
    PlaceSearchResult,
    ResolvedPlaceSearch,
)


def create_location() -> ResolvedLocation:
    """Create one resolved London location."""

    return ResolvedLocation(
        query="London",
        display_name="London, United Kingdom",
        latitude=51.5071,
        longitude=-0.1276,
    )


def create_place() -> PlaceOption:
    """Create one evidence-backed attraction."""

    return PlaceOption(
        name="British Museum",
        summary="A major museum covering human history and culture.",
        categories=["Museum", "History"],
        address="Great Russell Street, London",
        website_url="https://www.britishmuseum.org/",
        source_urls=[
            "https://www.britishmuseum.org/visit",
            "https://www.visitlondon.com/things-to-do/place/285709-british-museum",
        ],
    )


def test_place_search_input_normalizes_and_deduplicates_interests() -> None:
    """Repeated interests should not spend duplicate provider query space."""

    request = PlaceSearchInput(
        destination=" London, United Kingdom ",
        interests=[" Museums ", "parks", "museums"],
        family_friendly=True,
        max_results=3,
    )

    assert request.destination == "London, United Kingdom"
    assert request.interests == ["Museums", "parks"]
    assert request.family_friendly is True
    assert request.max_results == 3


@pytest.mark.parametrize(
    "values",
    [
        {"destination": " "},
        {"destination": "London", "interests": [" "]},
        {"destination": "London", "max_results": 0},
        {"destination": "London", "max_results": 6},
        {"destination": "London", "unexpected": True},
    ],
)
def test_place_search_input_rejects_invalid_values(values: dict[str, object]) -> None:
    """Invalid or unbounded discovery inputs should be rejected."""

    with pytest.raises(ValidationError):
        PlaceSearchInput.model_validate(values)


def test_resolved_place_search_preserves_validated_request_and_location() -> None:
    """Providers should receive one selected location with normalized preferences."""

    search = ResolvedPlaceSearch(
        request=PlaceSearchInput(
            destination=" London ",
            interests=[" Museums "],
        ),
        location=create_location(),
    )

    assert search.request.destination == "London"
    assert search.request.interests == ["Museums"]
    assert search.location.display_name == "London, United Kingdom"


def test_resolved_place_search_rejects_extra_or_invalid_nested_data() -> None:
    """Provider-ready searches should retain strict nested validation."""

    with pytest.raises(ValidationError):
        ResolvedPlaceSearch(
            request=PlaceSearchInput(destination="London"),
            location=create_location(),
            provider="tavily",
        )

    with pytest.raises(ValidationError):
        ResolvedPlaceSearch.model_validate(
            {
                "request": {"destination": " "},
                "location": create_location().model_dump(),
            }
        )


def test_place_option_requires_and_deduplicates_evidence_urls() -> None:
    """Every place should retain unique evidence for user-visible claims."""

    place = PlaceOption(
        name=" British Museum ",
        summary=" Museum summary ",
        source_urls=[
            "https://example.com/place",
            "https://example.com/place",
        ],
    )

    assert place.name == "British Museum"
    assert place.summary == "Museum summary"
    assert [str(url) for url in place.source_urls] == ["https://example.com/place"]


def test_place_option_preserves_provider_id_and_coordinate_pair() -> None:
    """Canonical provider identity and map coordinates should remain available."""

    place = PlaceOption(
        provider_place_id="google-place-123",
        name="British Museum",
        summary="A museum in London.",
        latitude=51.5194,
        longitude=-0.1270,
        source_urls=["https://maps.google.com/?cid=123"],
    )

    assert place.provider_place_id == "google-place-123"
    assert place.latitude == 51.5194
    assert place.longitude == -0.1270


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(51.5194, None), (None, -0.1270)],
)
def test_place_option_rejects_incomplete_coordinate_pair(
    latitude: float | None,
    longitude: float | None,
) -> None:
    """A map coordinate must never contain only one axis."""

    with pytest.raises(
        ValidationError,
        match="latitude and longitude must be provided together",
    ):
        PlaceOption(
            name="British Museum",
            summary="A museum in London.",
            latitude=latitude,
            longitude=longitude,
            source_urls=["https://maps.google.com/?cid=123"],
        )


def test_place_option_rejects_missing_sources_and_extra_fields() -> None:
    """Unsupported place claims and source-less results should be rejected."""

    with pytest.raises(ValidationError):
        PlaceOption(name="Museum", summary="Summary", source_urls=[])

    with pytest.raises(ValidationError):
        PlaceOption(
            name="Museum",
            summary="Summary",
            source_urls=["https://example.com/museum"],
            rating=5,
        )


def test_place_search_result_accepts_consistent_available_places() -> None:
    """Available status should preserve normalized places and evidence."""

    result = PlaceSearchResult(
        status=PlaceSearchStatus.PLACES_AVAILABLE,
        searched_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        location=create_location(),
        places=[create_place()],
    )

    assert result.status is PlaceSearchStatus.PLACES_AVAILABLE
    assert result.places[0].name == "British Museum"


def test_place_search_result_accepts_empty_no_places_response() -> None:
    """No-results status should remain a valid normalized outcome."""

    result = PlaceSearchResult(
        status=PlaceSearchStatus.NO_PLACES,
        searched_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        location=create_location(),
        message="No relevant places were found.",
    )

    assert result.places == []


@pytest.mark.parametrize(
    ("status", "places", "searched_at", "message"),
    [
        (
            PlaceSearchStatus.PLACES_AVAILABLE,
            [],
            datetime(2026, 8, 24, 12, tzinfo=UTC),
            "requires at least one place",
        ),
        (
            PlaceSearchStatus.NO_PLACES,
            [create_place()],
            datetime(2026, 8, 24, 12, tzinfo=UTC),
            "cannot contain places",
        ),
        (
            PlaceSearchStatus.NO_PLACES,
            [],
            datetime(2026, 8, 24, 12),
            "must include a timezone",
        ),
    ],
)
def test_place_search_result_rejects_inconsistent_outcomes(
    status: PlaceSearchStatus,
    places: list[PlaceOption],
    searched_at: datetime,
    message: str,
) -> None:
    """Status and timestamps should never contradict normalized output."""

    with pytest.raises(ValidationError, match=message):
        PlaceSearchResult(
            status=status,
            searched_at=searched_at,
            location=create_location(),
            places=places,
        )
