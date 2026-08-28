"""Cost-bounded canonical location resolution use case."""

from app.domain.trips import CanonicalLocation
from app.providers.locations.canonical_client import CanonicalLocationProvider


class LocationResolutionService:
    """Return provider-verified options for explicit user selection."""

    def __init__(self, *, provider: CanonicalLocationProvider) -> None:
        self.provider = provider

    async def resolve(
        self,
        *,
        query: str,
        max_results: int = 5,
    ) -> list[CanonicalLocation]:
        """Normalize one query and delegate exactly once to the provider."""

        normalized_query = query.strip()
        if not 2 <= len(normalized_query) <= 120:
            raise ValueError("query must contain between 2 and 120 characters")
        if not 1 <= max_results <= 5:
            raise ValueError("max_results must be between 1 and 5")
        return await self.provider.search_canonical_locations(
            query=normalized_query,
            max_results=max_results,
        )
