"""Tests for Duffel Places Suggestions response schemas."""

import pytest
from pydantic import ValidationError

from app.providers.airports.duffel_schemas import (
    DuffelPlaceSuggestion,
    DuffelPlaceSuggestionsResponse,
)


def create_suggestion(**overrides: object) -> DuffelPlaceSuggestion:
    """Create one valid Duffel place suggestion."""

    values: dict[str, object] = {
        "id": "arp_lhr_gb",
        "iata_code": "LHR",
        "type": "airport",
        "name": "Heathrow",
        "city_name": "London",
        "iata_country_code": "GB",
    }
    values.update(overrides)
    return DuffelPlaceSuggestion.model_validate(values)


def test_duffel_place_suggestion_parses_compact_fields_and_ignores_nested_data() -> (
    None
):
    """Only fields needed by normalized airport lookup should be retained."""

    suggestion = DuffelPlaceSuggestion.model_validate(
        {
            "id": " arp_lhr_gb ",
            "iata_code": " lhr ",
            "type": "airport",
            "name": " Heathrow ",
            "city_name": " London ",
            "iata_country_code": " gb ",
            "latitude": 51.47,
            "longitude": -0.4543,
            "time_zone": "Europe/London",
            "city": {"id": "cit_lon_gb", "iata_code": "LON"},
            "airports": [{"id": "arp_lgw_gb", "iata_code": "LGW"}],
        }
    )

    assert suggestion.model_dump() == {
        "id": "arp_lhr_gb",
        "iata_code": "LHR",
        "type": "airport",
        "name": "Heathrow",
        "city_name": "London",
        "iata_country_code": "GB",
    }


def test_duffel_city_suggestion_accepts_missing_city_name() -> None:
    """A metropolitan city result should not require a redundant city name."""

    suggestion = create_suggestion(
        id="cit_lon_gb",
        iata_code="LON",
        type="city",
        name="London",
        city_name=None,
    )

    assert suggestion.type == "city"
    assert suggestion.city_name is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": " "},
        {"iata_code": "LH"},
        {"iata_code": "L1R"},
        {"type": "country"},
        {"name": " "},
        {"city_name": " "},
        {"iata_country_code": "GBR"},
    ],
)
def test_duffel_place_suggestion_rejects_invalid_required_data(
    overrides: dict[str, object],
) -> None:
    """Malformed Duffel records should fail before normalized mapping."""

    with pytest.raises(ValidationError):
        create_suggestion(**overrides)


def test_duffel_response_accepts_warnings_and_empty_data() -> None:
    """Optional provider metadata should not enter the normalized response."""

    response = DuffelPlaceSuggestionsResponse.model_validate(
        {
            "warnings": [{"type": "deprecation", "message": "Example"}],
            "data": [],
        }
    )

    assert response.model_dump() == {"data": []}


def test_duffel_response_does_not_apply_normalized_five_result_limit() -> None:
    """The later mapper should bound a valid provider response, not parsing."""

    response = DuffelPlaceSuggestionsResponse(
        data=[
            create_suggestion(
                id=f"arp_{index}",
                iata_code=f"A{chr(65 + index)}A",
            )
            for index in range(6)
        ]
    )

    assert len(response.data) == 6
