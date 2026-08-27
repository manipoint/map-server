"""Map Duffel Places responses into normalized airport results."""

from app.providers.airports.duffel_schemas import (
    DuffelPlaceSuggestionsResponse,
)
from app.providers.airports.schemas import (
    AirportOption,
    AirportSearchInput,
    AirportSearchResult,
)


def map_duffel_airport_search(
    *,
    request: AirportSearchInput,
    response: DuffelPlaceSuggestionsResponse,
) -> AirportSearchResult:
    """Map ranked Duffel suggestions into bounded unique options."""

    options: list[AirportOption] = []
    seen_iata_codes: set[str] = set()

    for suggestion in response.data:
        if suggestion.iata_code in seen_iata_codes:
            continue

        options.append(
            AirportOption(
                provider_location_id=suggestion.id,
                iata_code=suggestion.iata_code,
                location_type=suggestion.type,
                name=suggestion.name,
                city_name=suggestion.city_name,
                country_name=None,
                country_code=suggestion.iata_country_code,
            )
        )
        seen_iata_codes.add(suggestion.iata_code)

        if len(options) == request.max_results:
            break

    return AirportSearchResult(
        query=request.query,
        options=options,
    )
