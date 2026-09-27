"""Batch metadata lookup contract for flight adapters."""

from typing import Protocol

from app.providers.flights.metadata_schemas import FlightMetadata


class FlightMetadataProvider(Protocol):
    """Resolve unique airport and carrier codes in one batch operation."""

    async def resolve(
        self,
        *,
        airport_codes: frozenset[str],
        carrier_codes: frozenset[str],
    ) -> FlightMetadata:
        """Return known metadata without inventing missing values."""
