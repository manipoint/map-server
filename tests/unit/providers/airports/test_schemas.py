"""Tests for provider-independent airport lookup schemas."""

import pytest
from pydantic import ValidationError

from app.providers.airports.schemas import (
    AirportOption,
    AirportResolution,
    AirportSearchInput,
    AirportSearchResult,
)


def create_airport(**overrides: object) -> AirportOption:
    """Create one valid normalized airport option."""

    values: dict[str, object] = {
        "provider_location_id": "apt_lhr",
        "iata_code": "LHR",
        "location_type": "airport",
        "name": "Heathrow Airport",
        "city_name": "London",
        "country_name": "United Kingdom",
        "country_code": "GB",
    }
    values.update(overrides)
    return AirportOption.model_validate(values)


def test_airport_search_input_normalizes_query_and_bounds_results() -> None:
    """Lookup text should be compact and use a small default result page."""

    request = AirportSearchInput(query="  London  ")

    assert request.query == "London"
    assert request.max_results == 5


@pytest.mark.parametrize(
    "values",
    [
        {"query": " "},
        {"query": "x" * 121},
        {"query": "London", "max_results": 0},
        {"query": "London", "max_results": 6},
        {"query": "London", "unexpected": True},
    ],
)
def test_airport_search_input_rejects_invalid_bounded_input(
    values: dict[str, object],
) -> None:
    """Invalid or unbounded lookup input should fail before provider work."""

    with pytest.raises(ValidationError):
        AirportSearchInput.model_validate(values)


def test_airport_option_normalizes_codes_and_builds_readable_label() -> None:
    """Provider codes and display text should use canonical public values."""

    option = create_airport(
        provider_location_id=" apt_lhr ",
        iata_code=" lhr ",
        name=" Heathrow Airport ",
        city_name=" London ",
        country_name=" United Kingdom ",
        country_code=" gb ",
    )

    assert option.iata_code == "LHR"
    assert option.country_code == "GB"
    assert option.display_name == ("Heathrow Airport, London, United Kingdom, LHR")
    assert "display_name" not in option.model_dump()


def test_airport_option_falls_back_to_country_code() -> None:
    """Duffel options without a country name should remain valid and readable."""

    option = create_airport(
        name="Heathrow",
        country_name=None,
    )

    assert option.country_name is None
    assert option.display_name == "Heathrow, London, GB, LHR"


@pytest.mark.parametrize("city_name", [None, "London", "london"])
def test_airport_option_avoids_repeating_city_name(
    city_name: str | None,
) -> None:
    """City-code labels should not repeat an equivalent city name."""

    option = create_airport(
        provider_location_id="city_lon",
        iata_code="LON",
        location_type="city",
        name="London",
        city_name=city_name,
    )

    assert option.display_name == "London, United Kingdom, LON"


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_location_id": " "},
        {"iata_code": "LH"},
        {"iata_code": "L1R"},
        {"location_type": "train_station"},
        {"name": " "},
        {"city_name": " "},
        {"country_name": " "},
        {"country_code": "GBR"},
        {"unexpected": True},
    ],
)
def test_airport_option_rejects_invalid_provider_data(
    overrides: dict[str, object],
) -> None:
    """Malformed provider options should not enter orchestration state."""

    with pytest.raises(ValidationError):
        create_airport(**overrides)


def test_airport_search_result_deduplicates_codes_in_provider_order() -> None:
    """The first ranked option for each normalized IATA code should win."""

    first_lhr = create_airport(provider_location_id="first-lhr")
    duplicate_lhr = create_airport(
        provider_location_id="second-lhr",
        iata_code="lhr",
    )
    gatwick = create_airport(
        provider_location_id="apt_lgw",
        iata_code="LGW",
        name="Gatwick Airport",
    )

    result = AirportSearchResult(
        query=" London ",
        options=[first_lhr, duplicate_lhr, gatwick],
    )

    assert result.query == "London"
    assert result.options == [first_lhr, gatwick]


def test_airport_search_result_accepts_no_matches() -> None:
    """An empty provider result should remain a valid lookup outcome."""

    result = AirportSearchResult(query="Unknown place")

    assert result.options == []


def test_airport_search_result_rejects_more_than_five_options() -> None:
    """Provider output should remain bounded before reaching an LLM."""

    options = [
        create_airport(
            provider_location_id=f"airport-{index}",
            iata_code=f"A{chr(65 + index)}A",
        )
        for index in range(6)
    ]

    with pytest.raises(ValidationError):
        AirportSearchResult(query="London", options=options)


@pytest.mark.parametrize(
    "resolution",
    [
        AirportResolution(status="resolved", query="LHR", iata_code="LHR"),
        AirportResolution(
            status="selection_required",
            query="London",
            options=[
                create_airport(iata_code="LHR"),
                create_airport(
                    provider_location_id="apt_lgw",
                    iata_code="LGW",
                    name="Gatwick Airport",
                ),
            ],
        ),
        AirportResolution(status="not_found", query="Unknown place"),
    ],
)
def test_airport_resolution_accepts_consistent_outcomes(
    resolution: AirportResolution,
) -> None:
    """Each public status should have one unambiguous payload shape."""

    assert resolution.query


@pytest.mark.parametrize(
    "values",
    [
        {"status": "resolved", "query": "London"},
        {
            "status": "resolved",
            "query": "London",
            "iata_code": "LHR",
            "options": [create_airport()],
        },
        {
            "status": "selection_required",
            "query": "London",
            "options": [create_airport()],
        },
        {
            "status": "selection_required",
            "query": "London",
            "iata_code": "LHR",
            "options": [create_airport(), create_airport(iata_code="LGW")],
        },
        {"status": "not_found", "query": "London", "iata_code": "LHR"},
        {"status": "not_found", "query": "London", "options": [create_airport()]},
    ],
)
def test_airport_resolution_rejects_inconsistent_outcomes(
    values: dict[str, object],
) -> None:
    """Contradictory status fields should fail before reaching the model."""

    with pytest.raises(ValidationError):
        AirportResolution.model_validate(values)
