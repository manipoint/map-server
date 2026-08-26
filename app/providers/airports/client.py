"""Airport lookup provider contract."""

from typing import Protocol

from app.providers.airports.schemas import (
    AirportSearchInput,
    AirportSearchResult,
)


class AirportProvider(Protocol):
    """Contract implemented by airport and city-code providers."""

    async def search_airports(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportSearchResult:
        """Return bounded airport or city-code options for one query."""
