"""Parse OpenFlights airline records into validated carrier metadata."""

import csv
from collections.abc import Iterable

from pydantic import ValidationError

from app.common.exceptions import ProviderConfigurationError
from app.providers.flights.metadata_schemas import AirlineMetadata

OPENFLIGHTS_COLUMN_COUNT = 8
MISSING_VALUES = frozenset({"", r"\N", "-"})


def build_airline_metadata(
    *,
    lines: Iterable[str],
) -> dict[str, AirlineMetadata]:
    """Import active IATA carriers without silently resolving conflicts."""
    airlines: dict[str, AirlineMetadata] = {}
    reader = csv.reader(lines, strict=True)

    try:
        for row in reader:
            if not row:
                continue

            if len(row) != OPENFLIGHTS_COLUMN_COUNT:
                raise ProviderConfigurationError(
                    f"Invalid airline column count near line {reader.line_num}"
                )

            # Columns: ID, name, alias, IATA, ICAO, callsign, country, active.
            active = row[7].strip().upper()

            if active not in {"Y", "N"}:
                raise ProviderConfigurationError(
                    f"Invalid airline active flag near line {reader.line_num}"
                )

            if active == "N":
                continue

            code = row[3].strip().upper()

            if code in MISSING_VALUES:
                continue

            # Do not mix ICAO three-letter codes into the IATA lookup.
            if len(code) != 2:
                raise ProviderConfigurationError(
                    f"Invalid airline IATA code near line {reader.line_num}"
                )

            name = row[1].strip()

            if name in MISSING_VALUES:
                raise ProviderConfigurationError(
                    f"Missing airline name near line {reader.line_num}"
                )

            try:
                airline = AirlineMetadata(
                    carrier_code=code,
                    name=name,
                )
            except ValidationError:
                raise ProviderConfigurationError(
                    f"Invalid airline record near line {reader.line_num}"
                ) from None

            existing = airlines.get(airline.carrier_code)

            if existing is not None and existing != airline:
                raise ProviderConfigurationError(
                    f"Conflicting airline names for IATA code: {airline.carrier_code}"
                )

            airlines[airline.carrier_code] = airline

    except csv.Error:
        raise ProviderConfigurationError(
            f"Invalid airline CSV near line {reader.line_num}"
        ) from None

    if not airlines:
        raise ProviderConfigurationError(
            "Airline source contains no usable active IATA carriers"
        )

    return {code: airlines[code] for code in sorted(airlines)}
