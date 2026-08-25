"""Build cost-bounded Google Places Text Search requests."""

from datetime import datetime
from typing import Final

from app.domain.places import PlaceSearchStatus
from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.google_schemas import (
    GooglePlaceResponse,
    GooglePlaceTextSearchRequest,
    GooglePlaceTextSearchResponse,
)
from app.providers.places.schemas import (
    PlaceOption,
    PlaceSearchInput,
    PlaceSearchResult,
    ResolvedPlaceSearch,
)

GOOGLE_PLACES_QUERY_MAX_LENGTH = 400
GOOGLE_PLACES_MAX_RESULTS = 5
GOOGLE_PLACES_MAX_QUERY_INTERESTS = 3
GOOGLE_PLACES_MAX_CATEGORIES = 3
IGNORED_GOOGLE_PLACE_TYPES: Final = frozenset(
    {
        "establishment",
        "point_of_interest",
    }
)


def build_google_place_search(
    *,
    request: PlaceSearchInput,
    location: ResolvedLocation,
) -> GooglePlaceTextSearchRequest:
    """Build one relevant and cost-bounded Google Places query."""

    selected_interests = request.interests[:GOOGLE_PLACES_MAX_QUERY_INTERESTS]
    if selected_interests:
        category_text = ", ".join(selected_interests)
        query = (
            f"Tourist attractions matching {category_text} in {location.display_name}"
        )
    else:
        query = f"Tourist attractions in {location.display_name}"

    if request.family_friendly is True:
        query = f"Family-friendly {query.lower()}"

    query = query[:GOOGLE_PLACES_QUERY_MAX_LENGTH].rstrip()

    return GooglePlaceTextSearchRequest(
        text_query=query,
        page_size=min(
            request.max_results,
            GOOGLE_PLACES_MAX_RESULTS,
        ),
        rank_preference="RELEVANCE",
    )


def normalize_google_categories(
    place: GooglePlaceResponse,
) -> list[str]:
    """Return unique human-readable categories in provider order."""

    categories: list[str] = []
    seen: set[str] = set()

    values = [place.primary_type, *place.types]

    for value in values:
        if value is None or value in IGNORED_GOOGLE_PLACE_TYPES:
            continue

        category = value.replace("_", " ").strip()
        key = category.casefold()

        if category and key not in seen:
            categories.append(category)
            seen.add(key)

        if len(categories) == GOOGLE_PLACES_MAX_CATEGORIES:
            break

    return categories


def build_google_place_summary(
    *,
    name: str,
    categories: list[str],
    address: str | None,
    location: ResolvedLocation,
) -> str:
    """Build a factual summary without an additional model call."""

    category = categories[0] if categories else "place to visit"
    destination = address or location.display_name

    return f"{name} is a {category} located at {destination}."


def map_google_place(
    *,
    place: GooglePlaceResponse,
    location: ResolvedLocation,
) -> PlaceOption | None:
    """Map one Google place while requiring a user-visible source."""

    if place.google_maps_uri is None:
        return None

    name = place.display_name.text[:200]
    categories = normalize_google_categories(place)
    coordinates = place.location

    return PlaceOption(
        provider_place_id=place.id,
        name=name,
        summary=build_google_place_summary(
            name=name,
            categories=categories,
            address=place.formatted_address,
            location=location,
        ),
        categories=categories,
        address=place.formatted_address,
        latitude=(coordinates.latitude if coordinates is not None else None),
        longitude=(coordinates.longitude if coordinates is not None else None),
        source_urls=[place.google_maps_uri],
    )


def map_google_place_search_response(
    *,
    response: GooglePlaceTextSearchResponse,
    search: ResolvedPlaceSearch,
    searched_at: datetime,
) -> PlaceSearchResult:
    """Build a bounded normalized result from Google Places."""

    if searched_at.utcoffset() is None:
        raise ValueError("searched_at must include a timezone")

    places: list[PlaceOption] = []
    seen_place_ids: set[str] = set()

    result_limit = min(
        search.request.max_results,
        GOOGLE_PLACES_MAX_RESULTS,
    )

    for google_place in response.places:
        if google_place.id in seen_place_ids:
            continue

        place = map_google_place(
            place=google_place,
            location=search.location,
        )

        if place is None:
            continue

        seen_place_ids.add(google_place.id)
        places.append(place)

        if len(places) == result_limit:
            break

    if not places:
        return PlaceSearchResult(
            status=PlaceSearchStatus.NO_PLACES,
            searched_at=searched_at,
            location=search.location,
            message="No relevant places were found for this search.",
        )

    return PlaceSearchResult(
        status=PlaceSearchStatus.PLACES_AVAILABLE,
        searched_at=searched_at,
        location=search.location,
        places=places,
    )
