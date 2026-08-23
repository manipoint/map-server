"""Flight provider contract."""

from typing import Protocol

from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult


class FlightProvider(Protocol):
    """Contract implemented by every external flight provider."""

    async def search_flights(
        self,
        *,
        request: FlightSearchInput,
    ) -> FlightSearchResult:
        """Search flights and return a normalized result."""
