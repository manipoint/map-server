"""Place-search orchestration use cases."""

from app.providers.locations.client import LocationProvider
from app.providers.places.client import PlaceProvider
from app.providers.places.schemas import (
    PlaceSearchInput,
    PlaceSearchResult,
    ResolvedPlaceSearch,
)
from app.services.location_selection import select_resolved_location

LOCATION_CANDIDATE_LIMIT = 5


class PlaceSearchService:
    """Resolve a destination and execute one bounded places search."""

    def __init__(
        self,
        *,
        location_provider: LocationProvider,
        place_provider: PlaceProvider,
    ) -> None:
        self.location_provider = location_provider
        self.place_provider = place_provider

    async def search_places(
        self,
        *,
        request: PlaceSearchInput,
    ) -> PlaceSearchResult:
        """Resolve one destination and delegate one provider search."""

        candidates = await self.location_provider.search_locations(
            query=request.destination,
            max_results=LOCATION_CANDIDATE_LIMIT,
        )

        selected_location = select_resolved_location(
            query=request.destination,
            candidates=candidates,
        )

        search = ResolvedPlaceSearch(
            request=request,
            location=selected_location,
        )

        return await self.place_provider.search_places(search=search)
