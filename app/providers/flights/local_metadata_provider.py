"""Flight metadata loaded once and resolved from memory."""

import asyncio
from pathlib import Path

from pydantic import ValidationError

from app.common.exceptions import ProviderConfigurationError
from app.providers.flights.metadata_schemas import FlightMetadata


class LocalFlightMetadataProvider:
    """Resolve requested codes from a validated metadata snapshot."""

    def __init__(self, *, metadata: FlightMetadata) -> None:
        snapshot = FlightMetadata.model_validate(metadata.model_dump())

        self._airports = snapshot.airports
        self._airlines = snapshot.airlines
        self._airport_codes = frozenset(snapshot.airports)

    @property
    def airport_codes(self) -> frozenset[str]:
        """Airport codes with validated timezone metadata."""
        return self._airport_codes

    @classmethod
    async def from_file(
        cls,
        *,
        path: Path,
    ) -> "LocalFlightMetadataProvider":
        """Load a trusted application-owned JSON file during startup."""

        try:
            content = await asyncio.to_thread(
                path.read_text,
                encoding="utf-8",
            )
            metadata = FlightMetadata.model_validate_json(content)

        except (OSError, UnicodeError, ValidationError):
            raise ProviderConfigurationError(
                "Flight metadata file is unavailable or invalid"
            ) from None

        return cls(metadata=metadata)

    async def resolve(
        self,
        *,
        airport_codes: frozenset[str],
        carrier_codes: frozenset[str],
    ) -> FlightMetadata:
        """Return known requested entries without inventing missing values."""

        return FlightMetadata(
            airports={
                code: self._airports[code]
                for code in airport_codes
                if code in self._airports
            },
            airlines={
                code: self._airlines[code]
                for code in carrier_codes
                if code in self._airlines
            },
        )
