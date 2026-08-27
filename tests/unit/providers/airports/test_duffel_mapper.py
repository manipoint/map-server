"""Tests for mapping Duffel place suggestions into airport results."""

from app.providers.airports.duffel_mapper import map_duffel_airport_search
from app.providers.airports.duffel_schemas import (
    DuffelPlaceSuggestion,
    DuffelPlaceSuggestionsResponse,
)
from app.providers.airports.schemas import AirportSearchInput


def create_suggestion(
    *,
    provider_id: str,
    iata_code: str,
    name: str,
    location_type: str = "airport",
    city_name: str | None = "London",
    country_code: str = "GB",
) -> DuffelPlaceSuggestion:
    """Create one valid provider suggestion for mapper tests."""

    return DuffelPlaceSuggestion.model_validate(
        {
            "id": provider_id,
            "iata_code": iata_code,
            "type": location_type,
            "name": name,
            "city_name": city_name,
            "iata_country_code": country_code,
        }
    )


def test_mapper_preserves_ranked_provider_fields() -> None:
    """One Duffel suggestion should become one compact normalized option."""

    request = AirportSearchInput(query=" London ", max_results=3)
    response = DuffelPlaceSuggestionsResponse(
        data=[
            create_suggestion(
                provider_id="cit_lon_gb",
                iata_code="LON",
                location_type="city",
                name="London",
                city_name=None,
            ),
            create_suggestion(
                provider_id="arp_lhr_gb",
                iata_code="LHR",
                name="Heathrow",
            ),
        ]
    )

    result = map_duffel_airport_search(request=request, response=response)

    assert result.query == "London"
    assert [option.iata_code for option in result.options] == ["LON", "LHR"]
    assert result.options[0].provider_location_id == "cit_lon_gb"
    assert result.options[0].location_type == "city"
    assert result.options[0].name == "London"
    assert result.options[0].country_name is None
    assert result.options[0].country_code == "GB"


def test_mapper_skips_duplicates_without_consuming_unique_limit() -> None:
    """Repeated IATA records should not prevent later unique ranked options."""

    request = AirportSearchInput(query="London", max_results=3)
    response = DuffelPlaceSuggestionsResponse(
        data=[
            create_suggestion(
                provider_id="first-lhr",
                iata_code="LHR",
                name="Heathrow",
            ),
            create_suggestion(
                provider_id="duplicate-lhr",
                iata_code="LHR",
                name="Heathrow duplicate",
            ),
            create_suggestion(
                provider_id="arp_lgw_gb",
                iata_code="LGW",
                name="Gatwick",
            ),
            create_suggestion(
                provider_id="arp_lcy_gb",
                iata_code="LCY",
                name="London City",
            ),
            create_suggestion(
                provider_id="arp_stn_gb",
                iata_code="STN",
                name="Stansted",
            ),
        ]
    )

    result = map_duffel_airport_search(request=request, response=response)

    assert [option.iata_code for option in result.options] == ["LHR", "LGW", "LCY"]
    assert result.options[0].provider_location_id == "first-lhr"


def test_mapper_honors_one_result_request() -> None:
    """A low-cost one-result lookup should stop after its first unique option."""

    request = AirportSearchInput(query="London", max_results=1)
    response = DuffelPlaceSuggestionsResponse(
        data=[
            create_suggestion(
                provider_id="arp_lhr_gb",
                iata_code="LHR",
                name="Heathrow",
            ),
            create_suggestion(
                provider_id="arp_lgw_gb",
                iata_code="LGW",
                name="Gatwick",
            ),
        ]
    )

    result = map_duffel_airport_search(request=request, response=response)

    assert [option.iata_code for option in result.options] == ["LHR"]


def test_mapper_preserves_empty_provider_result() -> None:
    """No Duffel suggestions should become a valid empty normalized result."""

    result = map_duffel_airport_search(
        request=AirportSearchInput(query="Unknown place"),
        response=DuffelPlaceSuggestionsResponse(),
    )

    assert result.query == "Unknown place"
    assert result.options == []
