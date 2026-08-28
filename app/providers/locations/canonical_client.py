"""Provider contract for canonical location resolution."""

from typing import Protocol

from app.domain.trips import CanonicalLocation


class CanonicalLocationProvider(Protocol):
    """Resolve bounded text queries into provider-qualified options."""

    async def search_canonical_locations(
        self,
        *,
        query: str,
        max_results: int,
    ) -> list[CanonicalLocation]: ...
