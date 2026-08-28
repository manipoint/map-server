"""Tests for canonical Google location mapping."""

import pytest

from app.providers.locations.google_mapper import map_google_location_options
from app.providers.places.google_schemas import GooglePlaceTextSearchResponse


def create_response(*places: dict[str, object]) -> GooglePlaceTextSearchResponse:
    """Validate provider-shaped place dictionaries for mapper tests."""

    return GooglePlaceTextSearchResponse.model_validate({"places": list(places)})


def create_place(**overrides: object) -> dict[str, object]:
    """Create one complete geographic Google result."""

    values: dict[str, object] = {
        "id": "london-id",
        "displayName": {"text": "London", "languageCode": "en"},
        "formattedAddress": "London, United Kingdom",
        "location": {"latitude": 51.5074, "longitude": -0.1278},
        "types": ["locality", "political"],
        "addressComponents": [
            {
                "longText": "United Kingdom",
                "shortText": "GB",
                "types": ["country", "political"],
            }
        ],
    }
    values.update(overrides)
    return values


def test_map_google_location_options_returns_provider_qualified_location() -> None:
    """A complete geographic result should become a canonical trip location."""

    options = map_google_location_options(
        response=create_response(create_place()),
        max_results=5,
    )

    assert [option.model_dump(mode="json") for option in options] == [
        {
            "provider": "google",
            "provider_location_id": "london-id",
            "canonical_name": "London, United Kingdom",
            "country_code": "GB",
            "latitude": 51.5074,
            "longitude": -0.1278,
        }
    ]


@pytest.mark.parametrize(
    "place",
    [
        create_place(types=["restaurant", "food"]),
        create_place(location=None),
        create_place(addressComponents=[]),
        create_place(
            addressComponents=[
                {
                    "longText": "United Kingdom",
                    "types": ["country", "political"],
                }
            ]
        ),
    ],
)
def test_map_google_location_options_skips_incomplete_or_business_results(
    place: dict[str, object],
) -> None:
    """The mapper must not guess identifiers, country codes, or coordinates."""

    assert (
        map_google_location_options(
            response=create_response(place),
            max_results=5,
        )
        == []
    )


def test_map_google_location_options_deduplicates_and_honors_limit() -> None:
    """Duplicate provider IDs and excess options should not escape the boundary."""

    options = map_google_location_options(
        response=create_response(
            create_place(),
            create_place(formattedAddress="Duplicate London"),
            create_place(
                id="london-ca-id",
                formattedAddress="London, ON, Canada",
                addressComponents=[
                    {
                        "longText": "Canada",
                        "shortText": "CA",
                        "types": ["country", "political"],
                    }
                ],
            ),
        ),
        max_results=1,
    )

    assert len(options) == 1
    assert options[0].provider_location_id == "london-id"


@pytest.mark.parametrize("max_results", [0, 6])
def test_map_google_location_options_rejects_unbounded_limit(
    max_results: int,
) -> None:
    """The mapper should enforce the endpoint's provider-cost boundary."""

    with pytest.raises(ValueError, match="between 1 and 5"):
        map_google_location_options(
            response=create_response(),
            max_results=max_results,
        )
