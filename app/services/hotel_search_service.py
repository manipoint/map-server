"""Hotel-search orchestration use cases."""

from datetime import timedelta

from app.common.exceptions import (
    InvalidTravelDateError,
)
from app.common.time import DateClock, utc_today
from app.providers.hotels.client import HotelProvider
from app.providers.hotels.schemas import (
    HotelSearchInput,
    HotelSearchResult,
    ResolvedHotelSearch,
)
from app.providers.locations.client import LocationProvider
from app.services.location_selection import select_resolved_location

LOCATION_CANDIDATE_LIMIT = 5


class HotelSearchService:
    """Resolve destinations and execute validated hotel searches."""

    def __init__(
        self,
        *,
        location_provider: LocationProvider,
        hotel_provider: HotelProvider,
        radius_km: int,
        clock: DateClock = utc_today,
    ) -> None:
        if not 1 <= radius_km <= 100:
            raise ValueError("radius_km must be between 1 and 100")
        self.location_provider = location_provider
        self.hotel_provider = hotel_provider
        self.radius_km = radius_km
        self.clock = clock

    async def search_hotels(self, *, request: HotelSearchInput) -> HotelSearchResult:
        today = self.clock()

        if request.check_in_date < today:
            raise InvalidTravelDateError("Hotel check-in date cannot be in the past")
        if request.check_in_date > today + timedelta(days=330):
            raise InvalidTravelDateError(
                "Hotel check-in date cannot be more than 330 days ahead"
            )

        candidates = await self.location_provider.search_locations(
            query=request.destination,
            max_results=LOCATION_CANDIDATE_LIMIT,
        )

        selected_location = select_resolved_location(
            query=request.destination,
            candidates=candidates,
        )

        search = ResolvedHotelSearch(
            request=request,
            location=selected_location,
            radius_km=self.radius_km,
        )

        return await self.hotel_provider.search_hotels(search=search)
