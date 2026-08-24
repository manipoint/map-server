"""Location search provider contract."""

from typing import Protocol

from app.providers.locations.schemas import ResolvedLocation


class LocationProvider(Protocol):
    """Contract implemented by deterministic location providers."""

    async def search_locations(
        self,
        *,
        query: str,
        max_results: int = 5,
    ) -> list[ResolvedLocation]:
        """Return ranked location candidates for a user query."""
