"""Build cost-bounded Tavily place-discovery requests."""

from app.providers.locations.schemas import ResolvedLocation
from app.providers.places.schemas import PlaceSearchInput
from app.providers.places.tavily_schemas import TavilyPlaceSearchRequest

TAVILY_QUERY_MAX_LENGTH = 400


def build_tavily_place_search(
    *, request: PlaceSearchInput, location: ResolvedLocation
) -> TavilyPlaceSearchRequest:
    """Build one deterministic and cost-bounded place-discovery query."""

    base = f"Best places to visit in {location.display_name}"
    suffix = " Family-friendly places only." if request.family_friendly else ""
    interest_prefix = ". Interests: "
    selected_interests: list[str] = []

    for interest in request.interests:
        candidate = ", ".join([*selected_interests, interest])
        query = f"{base}{interest_prefix}{candidate}{suffix}"

        if len(query) > TAVILY_QUERY_MAX_LENGTH:
            break
        selected_interests.append(interest)

    if selected_interests:
        query = f"{base}{interest_prefix}{', '.join(selected_interests)}{suffix}"
    else:
        query = f"{base}.{suffix}".strip()

    return TavilyPlaceSearchRequest(query=query, max_results=request.max_results)
