"""Convert airport source records into validated application datasets."""

from collections.abc import Iterable, Mapping

from pydantic import ValidationError

from app.common.exceptions import ProviderConfigurationError
from app.providers.airports.directory_schemas import AirportDirectory
from app.providers.airports.schemas import AirportOption
from app.providers.flights.metadata_schemas import (
    AirportMetadata,
    FlightMetadata,
)


def build_airport_datasets(
    *,
    records: Iterable[Mapping[str, object]],
    existing_metadata: FlightMetadata | None = None,
) -> tuple[AirportDirectory, FlightMetadata]:
    """Build matching airport datasets while retaining existing airlines."""

    directory_entries: dict[str, AirportOption] = {}
    timezone_entries: dict[str, AirportMetadata] = {}

    for position, record in enumerate(records, start=1):
        raw_code = record.get("iata")

        # Non-IATA airports are outside our flight-search contract.
        if raw_code is None:
            continue

        if isinstance(raw_code, str) and not raw_code.strip():
            continue

        city = record.get("city")
        if isinstance(city, str):
            city = city.strip() or None

        try:
            timezone = AirportMetadata.model_validate(
                {
                    "iata_code": raw_code,
                    "time_zone": record.get("tz"),
                }
            )

            airport = AirportOption.model_validate(
                {
                    "provider_location_id": (f"airportsdata:{timezone.iata_code}"),
                    "iata_code": timezone.iata_code,
                    "location_type": "airport",
                    "name": record.get("name"),
                    "city_name": city,
                    "country_code": record.get("country"),
                }
            )
        except ValidationError:
            raise ProviderConfigurationError(
                f"Invalid airport source record at position {position}"
            ) from None

        code = airport.iata_code

        if code in directory_entries:
            raise ProviderConfigurationError(f"Duplicate airport IATA code: {code}")

        directory_entries[code] = airport
        timezone_entries[code] = timezone

    if not directory_entries:
        raise ProviderConfigurationError(
            "Airport source contains no usable IATA airports"
        )

    ordered_codes = sorted(directory_entries)

    directory = AirportDirectory(
        airports=[directory_entries[code] for code in ordered_codes],
    )

    metadata = FlightMetadata(
        airports={code: timezone_entries[code] for code in ordered_codes},
        airlines=(
            dict(existing_metadata.airlines) if existing_metadata is not None else {}
        ),
    )

    return directory, metadata
