"""Flight-search orchestration and group-booking policy."""

from app.common.exceptions import InvalidTravelDateError
from app.common.time import UtcClock, utc_now
from app.domain.flights import FlightSearchStatus
from app.providers.flights.client import FlightProvider
from app.providers.flights.schemas import (
    FlightSearchInput,
    FlightSearchResult,
)

DEFAULT_SELF_SERVICE_TRAVELER_LIMIT = 9


class FlightSearchService:
    """Validate flight searches before calling an external provider."""

    def __init__(
        self,
        *,
        flight_provider: FlightProvider,
        self_service_traveler_limit: int = DEFAULT_SELF_SERVICE_TRAVELER_LIMIT,
        clock: UtcClock = utc_now,
    ) -> None:
        if self_service_traveler_limit < 1:
            raise ValueError("self_service_traveler_limit must be at least 1")

        self.flight_provider = flight_provider
        self.self_service_traveler_limit = self_service_traveler_limit
        self.clock = clock

    async def search_flights(
        self,
        *,
        request: FlightSearchInput,
    ) -> FlightSearchResult:
        """Return group guidance or execute one provider search."""
        searched_at = self.clock()

        if request.departure_date < searched_at.date():
            raise InvalidTravelDateError("Flight departure date cannot be in the past")

        if request.total_travelers > self.self_service_traveler_limit:
            return FlightSearchResult(
                status=FlightSearchStatus.GROUP_BOOKING_REQUIRED,
                searched_at=searched_at,
                offers=[],
                message=(
                    "Online flight search supports up to "
                    f"{self.self_service_traveler_limit} travelers. "
                    "Please request group-booking assistance. "
                    "Hotel, places, and weather planning can continue."
                ),
            )
        return await self.flight_provider.search_flights(request=request)
