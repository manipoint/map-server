"""Place-discovery provider contract."""

from typing import Protocol

from app.providers.places.schemas import (
    PlaceSearchResult,
    ResolvedPlaceSearch,
)


class PlaceProvider(Protocol):
    """Contract implemented by every place-discovery provider."""

    async def search_places(
        self,
        *,
        search: ResolvedPlaceSearch,
    ) -> PlaceSearchResult:
        """Search places and return normalized evidence-backed results."""
