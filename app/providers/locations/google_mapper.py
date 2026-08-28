"""Map Google Places geography results to canonical trip locations."""

from typing import Final

from app.domain.trips import CanonicalLocation
from app.providers.places.google_schemas import GooglePlaceTextSearchResponse

GOOGLE_LOCATION_PROVIDER: Final = "google"
GEOGRAPHIC_PLACE_TYPES: Final = frozenset(
    {
        "administrative_area_level_1",
        "administrative_area_level_2",
        "administrative_area_level_3",
        "country",
        "locality",
        "neighborhood",
        "postal_town",
        "sublocality",
        "sublocality_level_1",
        "sublocality_level_2",
    }
)


def map_google_location_options(
    *,
    response: GooglePlaceTextSearchResponse,
    max_results: int,
) -> list[CanonicalLocation]:
    """Return complete geographic options without guessing missing fields."""

    if not 1 <= max_results <= 5:
        raise ValueError("max_results must be between 1 and 5")

    options: list[CanonicalLocation] = []
    seen_ids: set[str] = set()
    for place in response.places:
        if place.id in seen_ids or not GEOGRAPHIC_PLACE_TYPES.intersection(place.types):
            continue
        if place.location is None:
            continue
        country_component = next(
            (
                component
                for component in place.address_components
                if "country" in component.types
            ),
            None,
        )
        if country_component is None or country_component.short_text is None:
            continue

        canonical_name = place.formatted_address or place.display_name.text
        try:
            option = CanonicalLocation(
                provider=GOOGLE_LOCATION_PROVIDER,
                provider_location_id=place.id,
                canonical_name=canonical_name[:200],
                country_code=country_component.short_text,
                latitude=place.location.latitude,
                longitude=place.location.longitude,
            )
        except ValueError:
            continue

        options.append(option)
        seen_ids.add(place.id)
        if len(options) == max_results:
            break

    return options
