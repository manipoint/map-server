"""Hotel provider contract."""

from typing import Protocol

from app.providers.hotels.schemas import (
    HotelSearchResult,
    ResolvedHotelSearch,
)


class HotelProvider(Protocol):
    """Contract implemented by every external hotel provider."""

    async def search_hotels(
        self,
        *,
        search: ResolvedHotelSearch,
    ) -> HotelSearchResult:
        """Search hotels and return normalized current availability."""
