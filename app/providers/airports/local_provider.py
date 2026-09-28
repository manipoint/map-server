"""Airport lookup using a validated local directory."""

import asyncio
from pathlib import Path

from pydantic import ValidationError

from app.common.exceptions import ProviderConfigurationError
from app.providers.airports.directory_schemas import AirportDirectory
from app.providers.airports.schemas import (
    AirportOption,
    AirportSearchInput,
    AirportSearchResult,
)


def _normalize_query(value: str) -> str:
    return " ".join(value.replace(",", " ").casefold().split())


class LocalAirportProvider:
    """Resolve exact airport names, city names and IATA codes."""

    def __init__(self, *, directory: AirportDirectory) -> None:
        snapshot = AirportDirectory.model_validate(directory.model_dump())
        self._airport_codes = frozenset(
            airport.iata_code for airport in snapshot.airports
        )
        index: dict[str, dict[str, AirportOption]] = {}

        for airport in snapshot.airports:
            names = {airport.name}

            if airport.city_name is not None:
                names.add(airport.city_name)

            aliases = {airport.iata_code, *names}

            for name in names:
                aliases.add(f"{name} {airport.country_code}")
                if airport.country_name is not None:
                    aliases.add(f"{name} {airport.country_name}")

            for alias in aliases:
                key = _normalize_query(alias)
                matches = index.setdefault(key, {})
                matches[airport.iata_code] = airport

        self._index: dict[str, tuple[AirportOption, ...]] = {
            key: tuple(matches[code] for code in sorted(matches))
            for key, matches in index.items()
        }

    @property
    def airport_codes(self) -> frozenset[str]:
        """Canonical directory codes for startup coverage validation."""
        return self._airport_codes

    @classmethod
    async def from_file(
        cls,
        *,
        path: Path,
    ) -> "LocalAirportProvider":
        """Load a trusted application-owned directory once."""

        try:
            content = await asyncio.to_thread(
                path.read_text,
                encoding="utf-8",
            )
            directory = AirportDirectory.model_validate_json(content)
        except (OSError, UnicodeError, ValidationError):
            raise ProviderConfigurationError(
                "Airport directory file is unavailable or invalid"
            ) from None

        return cls(directory=directory)

    async def search_airports(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportSearchResult:
        """Return bounded, deterministic matches without guessing."""

        matches = self._index.get(
            _normalize_query(request.query),
            (),
        )

        return AirportSearchResult(
            query=request.query,
            options=[
                airport.model_copy(deep=True)
                for airport in matches[: request.max_results]
            ],
        )
